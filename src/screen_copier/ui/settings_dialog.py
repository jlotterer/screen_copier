"""Settings dialog: every §8.1 setting plus the API key (architecture §8, §14, REQ-SES-02,
REQ-OCR-07, REQ-NFR-03).

The key is never shown with more than its last 4 characters: the display uses ``mask_key`` and
the entry field is cleared as soon as the key is stored. Slow work (listing cameras, which probes
DirectShow, and the "Test" request) runs on a thread pool, never on the GUI thread.

The dialog edits the ``Settings`` object it is given in place, so a controller sharing that object
sees the new values, then saves it with ``save_settings``. If the camera or exposure changed it
calls ``controller.select_camera`` so the camera worker restarts with them. After the user changes
the key in response to ``auth_required``, the caller resumes the queue.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import anthropic
from keyring.errors import KeyringError
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from screen_copier.camera.source import CameraInfo, list_cameras
from screen_copier.config.logging import redact
from screen_copier.config.secrets import (
    API_KEY_ENV,
    KeyringBackend,
    api_key_from_env,
    clear_api_key,
    get_api_key,
    keyring_available,
    mask_key,
    set_api_key,
)
from screen_copier.config.settings import DEFAULT_MODEL, LineEnding, Settings, save_settings
from screen_copier.models import CaptureMode
from screen_copier.recognition.claude import AUTH_STATUS, MissingApiKeyError

log = logging.getLogger(__name__)

KNOWN_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
TEST_PROMPT = "ping"

KEY_WORKS = "Key works"
KEY_REJECTED = "Key rejected"
KEY_UNREACHABLE = "Could not reach the API"
KEY_TESTING = "Testing…"

AUTH_REQUIRED_TEXT = (
    "Transcription is paused because the Anthropic API key is missing or was rejected. "
    "Your frames are kept. Enter a valid key below; waiting frames are sent once it is set."
)
KEYRING_UNAVAILABLE_TEXT = (
    "Secure key storage (Windows Credential Manager) is not available, so a key can't be saved "
    f"here. Set the {API_KEY_ENV} environment variable to your key and restart Screen Copier."
)
ENV_KEY_TEXT = (
    f"The key comes from the {API_KEY_ENV} environment variable, which takes priority over a "
    "stored key. To change it, change the environment variable."
)
KEY_MISSING_TEXT = "No API key is set. Enter your Anthropic API key and press Save Key."
KEY_STORED_TEXT = "The key is stored in Windows Credential Manager."


class SettingsController(Protocol):
    def select_camera(self, camera_id: int) -> None: ...


class _Bridge(QObject):
    """Delivers background results to the GUI thread (queued across threads)."""

    done = Signal(object, object)  # callback, value

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.done.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, then: Callable[[Any], None], value: Any) -> None:
        then(value)


class _Job(QRunnable):
    def __init__(self, bridge: _Bridge, work: Callable[[], Any], then: Callable[[Any], None]):
        super().__init__()
        self._bridge = bridge
        self._work = work
        self._then = then

    def run(self) -> None:
        try:
            value: Any = self._work()
        except Exception as exc:  # delivered to the callback, which reports it
            value = exc
        with contextlib.suppress(RuntimeError):  # the dialog was destroyed meanwhile
            self._bridge.done.emit(self._then, value)


def describe_test_error(exc: BaseException) -> str:
    """Map a "Test" failure to the short status shown in the dialog."""
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code in AUTH_STATUS:
        return KEY_REJECTED
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return KEY_UNREACHABLE
    if isinstance(exc, MissingApiKeyError):
        return "No API key is set"
    if isinstance(exc, anthropic.APIStatusError):
        return f"The API returned an error (HTTP {exc.status_code})"
    return f"Test failed: {redact(str(exc)) or type(exc).__name__}"


class SettingsDialog(QDialog):
    """Edit every setting and manage the API key. States: key-ok, key-missing,
    keyring-unavailable (plus key-from-environment)."""

    cameras_loaded = Signal()
    test_finished = Signal(str)

    def __init__(
        self,
        controller: SettingsController,
        settings: Settings,
        *,
        client_factory: Callable[[], Any],
        keyring_backend: KeyringBackend | None = None,
        env: Mapping[str, str] | None = None,
        cameras: Callable[[], list[CameraInfo]] = list_cameras,
        save: Callable[[Settings], None] = save_settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._client_factory = client_factory
        self._backend = keyring_backend
        self._env = env
        self._cameras = cameras
        self._save = save
        self._pool = QThreadPool(self)
        self._bridge = _Bridge(self)
        self.key_state = ""

        self.setWindowTitle("Settings")
        self.setModal(True)

        self.auth_label = QLabel(AUTH_REQUIRED_TEXT)
        self.auth_label.setWordWrap(True)
        self.auth_label.setStyleSheet("font-weight: bold;")
        self.auth_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.auth_label)
        layout.addWidget(self._build_key_group())
        layout.addWidget(self._build_camera_group())
        layout.addWidget(self._build_capture_group())
        layout.addWidget(self._build_recognition_group())
        layout.addWidget(self._build_output_group())

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

        self._load_values()
        self.refresh_key_state()
        self.reload_cameras()

    # ================================================================ building
    def _build_key_group(self) -> QGroupBox:
        group = QGroupBox("Anthropic API key")
        form = QFormLayout(group)

        self.key_field = QLineEdit()
        self.key_field.setReadOnly(True)
        self.key_field.setPlaceholderText("No key set")
        form.addRow("Current key:", self.key_field)

        self.key_status_label = QLabel("")
        self.key_status_label.setWordWrap(True)
        form.addRow(self.key_status_label)

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("sk-ant-…")
        self.save_key_button = QPushButton("Save Key")
        self.save_key_button.clicked.connect(self.save_key)
        self.key_input.returnPressed.connect(self.save_key)
        entry = QHBoxLayout()
        entry.addWidget(self.key_input)
        entry.addWidget(self.save_key_button)
        form.addRow("New key:", entry)

        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self.test_key)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_key)
        self.test_result_label = QLabel("")
        actions = QHBoxLayout()
        actions.addWidget(self.test_button)
        actions.addWidget(self.clear_button)
        actions.addWidget(self.test_result_label, 1)
        form.addRow(actions)
        return group

    def _build_camera_group(self) -> QGroupBox:
        group = QGroupBox("Camera")
        form = QFormLayout(group)
        self.camera_combo = QComboBox()
        form.addRow("Camera:", self.camera_combo)
        self.auto_exposure_check = QCheckBox("Automatic exposure")
        self.auto_exposure_check.toggled.connect(self._sync_exposure_enabled)
        form.addRow(self.auto_exposure_check)
        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(-20.0, 20.0)
        self.exposure_spin.setDecimals(1)
        self.exposure_spin.setSingleStep(1.0)
        self.exposure_spin.setToolTip("Driver exposure value (DirectShow: log2 of seconds).")
        form.addRow("Exposure:", self.exposure_spin)
        return group

    def _build_capture_group(self) -> QGroupBox:
        group = QGroupBox("Capture")
        form = QFormLayout(group)
        self.capture_mode_combo = QComboBox()
        self.capture_mode_combo.addItem("Auto", CaptureMode.AUTO)
        self.capture_mode_combo.addItem("Manual", CaptureMode.MANUAL)
        form.addRow("Default capture mode:", self.capture_mode_combo)
        self.stable_duration_spin = _int_spin(0, 60_000, " ms")
        form.addRow("Stable for:", self.stable_duration_spin)
        self.stability_spin = _float_spin(0.0, 255.0, 2)
        form.addRow("Stability threshold:", self.stability_spin)
        self.motion_spin = _float_spin(0.0, 255.0, 2)
        form.addRow("Motion threshold:", self.motion_spin)
        self.sharpness_spin = _float_spin(0.0, 100_000.0, 1)
        form.addRow("Sharpness threshold:", self.sharpness_spin)
        self.duplicate_spin = _float_spin(0.0, 255.0, 2)
        form.addRow("Duplicate threshold:", self.duplicate_spin)
        self.max_edge_spin = _int_spin(1, 8192, " px")
        form.addRow("Max image edge:", self.max_edge_spin)
        return group

    def _build_recognition_group(self) -> QGroupBox:
        group = QGroupBox("Recognition and stitching")
        form = QFormLayout(group)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(KNOWN_MODELS)
        form.addRow("Model:", self.model_combo)
        self.concurrency_spin = _int_spin(1, 16, "")
        form.addRow("Parallel requests:", self.concurrency_spin)
        self.min_overlap_spin = _int_spin(1, 100, " lines")
        form.addRow("Minimum overlap:", self.min_overlap_spin)
        self.line_similarity_spin = _float_spin(0.0, 1.0, 2)
        self.line_similarity_spin.setSingleStep(0.01)
        form.addRow("Line similarity:", self.line_similarity_spin)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Output and sessions")
        form = QFormLayout(group)
        self.line_ending_combo = QComboBox()
        self.line_ending_combo.addItem("LF (\\n)", LineEnding.LF)
        self.line_ending_combo.addItem("CRLF (\\r\\n)", LineEnding.CRLF)
        form.addRow("Line ending:", self.line_ending_combo)
        self.sound_check = QCheckBox("Play a sound on capture")
        form.addRow(self.sound_check)
        self.retention_spin = _int_spin(0, 3650, " days")
        form.addRow("Keep saved sessions for:", self.retention_spin)
        return group

    # ================================================================ values
    def _load_values(self) -> None:
        s = self._settings
        self.auto_exposure_check.setChecked(s.auto_exposure)
        self.exposure_spin.setValue(s.exposure if s.exposure is not None else -6.0)
        self._sync_exposure_enabled()
        _select_data(self.capture_mode_combo, s.capture_mode)
        self.stable_duration_spin.setValue(s.stable_duration_ms)
        self.stability_spin.setValue(s.stability_threshold)
        self.motion_spin.setValue(s.motion_threshold)
        self.sharpness_spin.setValue(s.sharpness_threshold)
        self.duplicate_spin.setValue(s.duplicate_threshold)
        self.max_edge_spin.setValue(s.max_image_edge_px)
        self.model_combo.setCurrentText(s.model)
        self.concurrency_spin.setValue(s.max_concurrent_requests)
        self.min_overlap_spin.setValue(s.min_overlap_lines)
        self.line_similarity_spin.setValue(s.line_similarity)
        _select_data(self.line_ending_combo, s.line_ending)
        self.sound_check.setChecked(s.sound_enabled)
        self.retention_spin.setValue(s.session_retention_days)
        self._set_camera_items([CameraInfo(s.camera_id, f"Camera {s.camera_id}")])

    def _sync_exposure_enabled(self) -> None:
        self.exposure_spin.setEnabled(not self.auto_exposure_check.isChecked())

    def edited_settings(self) -> Settings:
        """A validated ``Settings`` with the dialog's values (raises ``ValidationError``)."""
        auto_exposure = self.auto_exposure_check.isChecked()
        camera_id = self.camera_combo.currentData()
        values = self._settings.model_dump()
        values.update(
            camera_id=self._settings.camera_id if camera_id is None else int(camera_id),
            auto_exposure=auto_exposure,
            exposure=self._settings.exposure if auto_exposure else self.exposure_spin.value(),
            capture_mode=self.capture_mode_combo.currentData(),
            stable_duration_ms=self.stable_duration_spin.value(),
            stability_threshold=self.stability_spin.value(),
            motion_threshold=self.motion_spin.value(),
            sharpness_threshold=self.sharpness_spin.value(),
            duplicate_threshold=self.duplicate_spin.value(),
            max_image_edge_px=self.max_edge_spin.value(),
            model=self.model_combo.currentText().strip() or DEFAULT_MODEL,
            max_concurrent_requests=self.concurrency_spin.value(),
            min_overlap_lines=self.min_overlap_spin.value(),
            line_similarity=self.line_similarity_spin.value(),
            line_ending=self.line_ending_combo.currentData(),
            sound_enabled=self.sound_check.isChecked(),
            session_retention_days=self.retention_spin.value(),
        )
        return Settings.model_validate(values)

    def accept(self) -> None:
        try:
            new = self.edited_settings()
        except ValueError as exc:
            self.error_label.setText(f"Invalid setting: {exc}")
            return
        old = self._settings
        camera_changed = (new.camera_id, new.auto_exposure, new.exposure) != (
            old.camera_id,
            old.auto_exposure,
            old.exposure,
        )
        for name in Settings.model_fields:
            value = getattr(new, name)
            if getattr(old, name) != value:
                setattr(old, name, value)
        try:
            self._save(old)
        except OSError as exc:
            log.exception("Could not save settings")
            self.error_label.setText(f"Could not save settings: {exc}")
            return
        if camera_changed:
            self._controller.select_camera(old.camera_id)
        super().accept()

    # ================================================================ cameras
    def reload_cameras(self) -> None:
        """List cameras in the background; ``cameras_loaded`` fires once the picker is filled."""
        self._run(self._cameras, self._on_cameras)

    def _on_cameras(self, value: Any) -> None:
        if isinstance(value, Exception):
            log.warning("Could not list cameras: %s", value)
        else:
            self._set_camera_items(list(value))
        self.cameras_loaded.emit()

    def _set_camera_items(self, cameras: list[CameraInfo]) -> None:
        wanted = self._settings.camera_id
        if not any(c.id == wanted for c in cameras):
            cameras = [*cameras, CameraInfo(wanted, f"Camera {wanted} (not connected)")]
        self.camera_combo.clear()
        for camera in cameras:
            self.camera_combo.addItem(camera.name, camera.id)
        _select_data(self.camera_combo, wanted)

    def camera_names(self) -> list[str]:
        return [self.camera_combo.itemText(i) for i in range(self.camera_combo.count())]

    # ================================================================ API key
    def refresh_key_state(self) -> None:
        """Show the masked key and enable the actions for the current key source."""
        from_env = api_key_from_env(self._env)
        storable = keyring_available(self._backend)
        key = get_api_key(self._backend, self._env)
        self.key_field.setText(mask_key(key))
        if from_env:
            self.key_state = "environment"
            text = ENV_KEY_TEXT
        elif not storable:
            self.key_state = "keyring-unavailable"
            text = KEYRING_UNAVAILABLE_TEXT
        elif key:
            self.key_state = "key-ok"
            text = KEY_STORED_TEXT
        else:
            self.key_state = "key-missing"
            text = KEY_MISSING_TEXT
        self.key_status_label.setText(text)
        self.key_input.setEnabled(storable)
        self.save_key_button.setEnabled(storable)
        self.clear_button.setEnabled(storable and not from_env and key is not None)
        self.test_button.setEnabled(key is not None)

    def save_key(self) -> None:
        key = self.key_input.text().strip()
        self.key_input.clear()
        if not key:
            self.test_result_label.setText("Enter a key first")
            return
        try:
            set_api_key(key, self._backend)
        except (KeyringError, RuntimeError) as exc:
            log.warning("Could not store the API key: %s", redact(str(exc)))
            self.test_result_label.setText("Could not store the key")
        else:
            self.test_result_label.setText("Key saved")
        self.refresh_key_state()

    def clear_key(self) -> None:
        try:
            clear_api_key(self._backend)
        except (KeyringError, RuntimeError) as exc:
            log.warning("Could not remove the API key: %s", redact(str(exc)))
            self.test_result_label.setText("Could not remove the key")
        else:
            self.test_result_label.setText("Key removed")
        self.refresh_key_state()

    def test_key(self) -> None:
        """Send a 1-token request in the background; the result lands in ``test_result_label``."""
        model = self.model_combo.currentText().strip() or DEFAULT_MODEL
        factory = self._client_factory

        def work() -> None:
            factory().messages.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": TEST_PROMPT}],
            )

        self.test_button.setEnabled(False)
        self.test_result_label.setText(KEY_TESTING)
        self._run(work, self._on_test_done)

    def _on_test_done(self, value: Any) -> None:
        text = describe_test_error(value) if isinstance(value, Exception) else KEY_WORKS
        self.test_result_label.setText(text)
        self.refresh_key_state()
        self.test_finished.emit(text)

    # ================================================================ auth_required
    def show_auth_required(self) -> None:
        """Open (non-blocking) with the explanation for a missing or rejected key (REQ-OCR-07)."""
        self.set_auth_required(True)
        self.refresh_key_state()
        self.open()
        self.key_input.setFocus()

    def set_auth_required(self, active: bool) -> None:
        self.auth_label.setVisible(active)

    # ================================================================ background work
    def _run(self, work: Callable[[], Any], then: Callable[[Any], None]) -> None:
        self._pool.start(_Job(self._bridge, work, then))

    def wait_for_background(self, timeout_ms: int = 10_000) -> bool:
        return self._pool.waitForDone(timeout_ms)


def _int_spin(low: int, high: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSuffix(suffix)
    return spin


def _float_spin(low: float, high: float, decimals: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(decimals)
    spin.setRange(low, high)
    return spin


def _select_data(combo: QComboBox, data: object) -> None:
    index = combo.findData(data)
    if index >= 0:
        combo.setCurrentIndex(index)
