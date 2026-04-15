import sys
import threading
from PyQt5 import QtCore
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, QStackedWidget,
                             QLabel, QComboBox, QPushButton, QHBoxLayout, QLineEdit, QRadioButton, QFrame)
import pyqtgraph as pg
import numpy as np
import utils as u
import scipy
import pandas as pd
import csv
import random
import serial
import neurokit2 as nk
import sounddevice as sd


DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #000000;
    color: #FFFFFF;
}
QLabel {
    color: #FFFFFF;
}
QPushButton {
    background-color: #1a1a2e;
    color: #FFFFFF;
    border: 1px solid #333355;
    border-radius: 4px;
    padding: 8px 16px;
    font-size: 14px;
}
QPushButton:hover {
    background-color: #16213e;
}
QPushButton:pressed {
    background-color: #0f3460;
}
QComboBox {
    background-color: #1a1a2e;
    color: #FFFFFF;
    border: 1px solid #333355;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox QAbstractItemView {
    background-color: #1a1a2e;
    color: #FFFFFF;
    selection-background-color: #0f3460;
}
QLineEdit {
    background-color: #1a1a2e;
    color: #FFFFFF;
    border: 1px solid #333355;
    border-radius: 4px;
    padding: 4px 8px;
}
"""


def filtrar_señal(señal, fs, correct_signal=False, moving_average=False, asd=True):
    if asd == False:
        return señal
    elif correct_signal:
        return u.integrador(u.correct_signal(u.pasabanda(u.med_filt(señal, fs), fs=fs, lowcut=0.5, highcut=50), fs), fs, largo_ventana=0.01)
    else:
        if moving_average:
            return u.integrador(u.pasabanda(u.med_filt(señal, fs), fs=fs, lowcut=0.5, highcut=50), fs, largo_ventana=0.05)
        else:
            return u.pasabanda(u.med_filt(señal, fs), fs=fs, lowcut=0.5, highcut=50)


# --- Audio Manager ---

class AudioManager:
    def __init__(self):
        self.sample_rate = 44100
        self.muted = False
        self.alarm_silenced = False
        self.alarm_active = False
        self._alarm_thread = None
        self._alarm_stop_event = threading.Event()

    def _generate_tone(self, frequency, duration_ms, volume=0.3):
        t = np.linspace(0, duration_ms / 1000, int(self.sample_rate * duration_ms / 1000), endpoint=False)
        # Apply envelope to avoid clicks
        tone = volume * np.sin(2 * np.pi * frequency * t)
        fade = min(int(0.005 * self.sample_rate), len(t) // 4)
        if fade > 0:
            tone[:fade] *= np.linspace(0, 1, fade)
            tone[-fade:] *= np.linspace(1, 0, fade)
        return tone.astype(np.float32)

    def play_heartbeat_beep(self, spo2):
        if self.muted:
            return
        # Pitch varies with SpO2: 400 Hz at 0%, 1000 Hz at 100%
        freq = 400 + (spo2 / 100.0) * 600
        freq = max(400, min(1000, freq))
        tone = self._generate_tone(freq, 80, volume=0.25)
        try:
            sd.play(tone, self.sample_rate)
        except Exception:
            pass

    def start_flatline_alarm(self):
        if self.alarm_active:
            return
        self.alarm_active = True
        self._alarm_stop_event.clear()
        self._alarm_thread = threading.Thread(target=self._alarm_loop, daemon=True)
        self._alarm_thread.start()

    def _alarm_loop(self):
        toggle = False
        while not self._alarm_stop_event.is_set():
            if not self.muted and not self.alarm_silenced:
                freq = 1000 if toggle else 800
                tone = self._generate_tone(freq, 300, volume=0.4)
                try:
                    sd.play(tone, self.sample_rate)
                    sd.wait()
                except Exception:
                    pass
                toggle = not toggle
            self._alarm_stop_event.wait(0.5)

    def stop_alarm(self):
        self.alarm_active = False
        self._alarm_stop_event.set()
        if self._alarm_thread:
            self._alarm_thread.join(timeout=2)
            self._alarm_thread = None
        try:
            sd.stop()
        except Exception:
            pass

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted:
            try:
                sd.stop()
            except Exception:
                pass
        return self.muted

    def silence_alarms(self, duration_sec=120):
        self.alarm_silenced = True
        # Will be reset by a QTimer in the GUI
        return duration_sec


# --- Capnography waveform ---

def generate_co2_waveform(duration, fs, resp_rate, etco2):
    total_samples = int(duration * fs)
    signal = np.zeros(total_samples)
    if resp_rate <= 0:
        return signal.tolist()

    breath_period = 60.0 / resp_rate
    breath_samples = int(breath_period * fs)
    if breath_samples < 4:
        return signal.tolist()

    # Phase fractions of one breath cycle
    insp_frac = 0.4    # inspiratory phase (baseline ~0)
    upstroke_frac = 0.08
    plateau_frac = 0.35
    downstroke_frac = 0.17

    for start in range(0, total_samples, breath_samples):
        end = min(start + breath_samples, total_samples)
        n = end - start

        n_insp = int(n * insp_frac)
        n_up = int(n * upstroke_frac)
        n_plat = int(n * plateau_frac)
        n_down = n - n_insp - n_up - n_plat

        # Build one breath
        breath = np.zeros(n)
        idx = n_insp  # inspiratory baseline stays 0
        # Expiratory upstroke
        if n_up > 0:
            breath[idx:idx + n_up] = np.linspace(0, etco2, n_up)
            idx += n_up
        # Alveolar plateau
        if n_plat > 0:
            breath[idx:idx + n_plat] = etco2
            idx += n_plat
        # Inspiratory downstroke
        if n_down > 0:
            breath[idx:idx + n_down] = np.linspace(etco2, 0, n_down)

        signal[start:end] = breath[:end - start]

    return signal.tolist()


# --- Parameter display widget ---

def create_param_frame(name, value_text, unit, color_hex):
    frame = QFrame()
    frame.setStyleSheet(f"""
        QFrame {{
            border: 1px solid #333355;
            border-radius: 6px;
            background-color: #0a0a14;
            padding: 4px;
        }}
    """)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(2)

    name_label = QLabel(name)
    name_label.setFont(QFont("Arial", 12))
    name_label.setStyleSheet(f"color: {color_hex}; border: none;")

    value_label = QLabel(value_text)
    value_label.setFont(QFont("Arial", 40, QFont.Bold))
    value_label.setStyleSheet(f"color: {color_hex}; border: none;")
    value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    unit_label = QLabel(unit)
    unit_label.setFont(QFont("Arial", 12))
    unit_label.setStyleSheet(f"color: {color_hex}; border: none;")
    unit_label.setAlignment(QtCore.Qt.AlignRight)

    layout.addWidget(name_label)
    layout.addWidget(value_label)
    layout.addWidget(unit_label)

    return frame, value_label


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setGeometry(200, 200, 2800, 1500)
        self.setWindowTitle("Monitor Multiparamétrico")
        self.setStyleSheet(DARK_STYLESHEET)

        # Serial port
        try:
            self.arduino = serial.Serial('COM3', 9600)
        except:
            print("Could not connect to Arduino")
            self.arduino = None

        # Load signals
        self.signals = []
        labels = []

        with open("labels.csv", 'r') as csv_file:
            csv_reader = csv.reader(csv_file)
            for row in csv_reader:
                labels.append(row)

        with open("signals.csv", 'r') as csv_file:
            csv_reader = csv.reader(csv_file)
            for row in csv_reader:
                row_as_float = [float(value) for value in row]
                self.signals.append(row_as_float)

        # Parameters
        self.heart_rate = 60
        self.spo2 = 98
        self.resp_rate = 12
        self.etco2 = 38
        self.nibp_sys = 120
        self.nibp_dia = 80
        self.temperature = 36.6

        self.labels = pd.DataFrame(labels, columns=["Rythm", "Index", "Lead"])

        # Audio
        self.audio_manager = AudioManager()

        # Cardiac arrest state
        self.cardiac_arrest_active = False
        self.alarm_flash_visible = False
        self.spo2_decay_value = 98.0

        # Main screen
        self.main_screen_widget = QWidget()
        self.setup_main_screen()

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self.main_screen_widget)
        self.setCentralWidget(self.stacked_widget)

    def setup_main_screen(self):
        title_label = QLabel("Simulador de Monitor Multiparamétrico", self)
        title_label.setFont(QFont("Arial", 22, QFont.Bold))
        title_label.setStyleSheet("color: #00FF00;")

        dropdown_rythm_label = QLabel("Ritmo:", self)
        dropdown_rythm_label.setFont(QFont("Arial", 14))
        dropdown_lead_label = QLabel("Derivación:", self)
        dropdown_lead_label.setFont(QFont("Arial", 14))

        dropdown_rythm = QComboBox(self)
        dropdown_rythm.setObjectName("dropdown_rythm")
        dropdown_rythm.setFont(QFont("Arial", 14))
        dropdown_rythm.addItems(["Ritmo Sinusal", "Fibrilacion auricular", "Marcapasos", "Arritmia Supraventricular"])

        dropdown_lead = QComboBox(self)
        dropdown_lead.setObjectName("dropdown_lead")
        dropdown_lead.setFont(QFont("Arial", 14))
        dropdown_lead.addItems(["I", "II", "III", "aVF", "aVR", "aVL", "V1", "V2", "V3", "V4", "V5", "V6"])

        # Parameter controls
        params_label = QLabel("Parámetros:", self)
        params_label.setFont(QFont("Arial", 16, QFont.Bold))
        params_label.setStyleSheet("color: #00FF00;")

        hr_label = QLabel("Frecuencia cardíaca (bpm):", self)
        hr_label.setFont(QFont("Arial", 12))
        self.hr_input = QLineEdit(str(self.heart_rate), self)
        self.hr_input.setFont(QFont("Arial", 12))

        spo2_label = QLabel("SpO2 (%):", self)
        spo2_label.setFont(QFont("Arial", 12))
        self.spo2_input = QLineEdit(str(self.spo2), self)
        self.spo2_input.setFont(QFont("Arial", 12))

        rr_label = QLabel("Frecuencia respiratoria (rpm):", self)
        rr_label.setFont(QFont("Arial", 12))
        self.rr_input = QLineEdit(str(self.resp_rate), self)
        self.rr_input.setFont(QFont("Arial", 12))

        etco2_label = QLabel("EtCO2 (mmHg):", self)
        etco2_label.setFont(QFont("Arial", 12))
        self.etco2_input = QLineEdit(str(self.etco2), self)
        self.etco2_input.setFont(QFont("Arial", 12))

        nibp_label = QLabel("NIBP Sistólica / Diastólica (mmHg):", self)
        nibp_label.setFont(QFont("Arial", 12))
        nibp_layout = QHBoxLayout()
        self.nibp_sys_input = QLineEdit(str(self.nibp_sys), self)
        self.nibp_sys_input.setFont(QFont("Arial", 12))
        self.nibp_dia_input = QLineEdit(str(self.nibp_dia), self)
        self.nibp_dia_input.setFont(QFont("Arial", 12))
        nibp_sep = QLabel("/", self)
        nibp_sep.setFont(QFont("Arial", 12))
        nibp_layout.addWidget(self.nibp_sys_input)
        nibp_layout.addWidget(nibp_sep)
        nibp_layout.addWidget(self.nibp_dia_input)

        temp_label = QLabel("Temperatura (°C):", self)
        temp_label.setFont(QFont("Arial", 12))
        self.temp_input = QLineEdit(str(self.temperature), self)
        self.temp_input.setFont(QFont("Arial", 12))

        params_layout = QVBoxLayout()
        params_layout.addWidget(params_label)
        params_layout.addWidget(hr_label)
        params_layout.addWidget(self.hr_input)
        params_layout.addWidget(spo2_label)
        params_layout.addWidget(self.spo2_input)
        params_layout.addWidget(rr_label)
        params_layout.addWidget(self.rr_input)
        params_layout.addWidget(etco2_label)
        params_layout.addWidget(self.etco2_input)
        params_layout.addWidget(nibp_label)
        params_layout.addLayout(nibp_layout)
        params_layout.addWidget(temp_label)
        params_layout.addWidget(self.temp_input)

        start_button = QPushButton("Comenzar", self)
        start_button.setFont(QFont("Arial", 16, QFont.Bold))
        start_button.setStyleSheet("""
            QPushButton {
                background-color: #004400;
                color: #00FF00;
                border: 2px solid #00FF00;
                border-radius: 8px;
                padding: 12px 32px;
            }
            QPushButton:hover { background-color: #006600; }
        """)
        start_button.clicked.connect(self.start_simulation)

        # Layout
        layout = QVBoxLayout(self.main_screen_widget)
        dropdown_label_layout = QHBoxLayout()
        dropdown_layout = QHBoxLayout()

        dropdown_label_layout.addWidget(dropdown_rythm_label, alignment=QtCore.Qt.AlignHCenter)
        dropdown_layout.addWidget(dropdown_rythm)
        dropdown_label_layout.addWidget(dropdown_lead_label, alignment=QtCore.Qt.AlignHCenter)
        dropdown_layout.addWidget(dropdown_lead)

        layout.addWidget(title_label, alignment=QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        layout.addStretch(1)
        layout.addLayout(dropdown_label_layout)
        layout.setSpacing(0)
        layout.addLayout(dropdown_layout)
        layout.addLayout(params_layout)
        layout.addStretch(1)
        layout.addWidget(start_button, alignment=QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter)

    def start_simulation(self):
        # Read parameter inputs
        try:
            self.heart_rate = int(self.hr_input.text())
        except ValueError:
            self.heart_rate = 60
        try:
            self.spo2 = int(self.spo2_input.text())
        except ValueError:
            self.spo2 = 98
        try:
            self.resp_rate = int(self.rr_input.text())
        except ValueError:
            self.resp_rate = 12
        try:
            self.etco2 = int(self.etco2_input.text())
        except ValueError:
            self.etco2 = 38
        try:
            self.nibp_sys = int(self.nibp_sys_input.text())
        except ValueError:
            self.nibp_sys = 120
        try:
            self.nibp_dia = int(self.nibp_dia_input.text())
        except ValueError:
            self.nibp_dia = 80
        try:
            self.temperature = float(self.temp_input.text())
        except ValueError:
            self.temperature = 36.6

        self.spo2_decay_value = float(self.spo2)
        self.cardiac_arrest_active = False

        selected_option_rythm = self.main_screen_widget.findChild(QComboBox, "dropdown_rythm").currentText()
        selected_option_lead = self.main_screen_widget.findChild(QComboBox, "dropdown_lead").currentText()

        dynamic_plot_widget = QWidget()
        self.leads_well_placed = True
        self.plotSignal(dynamic_plot_widget, selected_option_rythm, selected_option_lead)

        self.stacked_widget.addWidget(dynamic_plot_widget)
        self.stacked_widget.setCurrentWidget(dynamic_plot_widget)

    def on_finished(self):
        self.timer.stop()
        self.plot_widget.clear()

    def generateSignals(self, selected_option_rythm, selected_option_lead):
        self.fs = 150
        duration = 60

        if selected_option_rythm == "Ritmo Sinusal":
            self.X_filt_real = nk.ecg_simulate(duration=duration, sampling_rate=self.fs, heart_rate=self.heart_rate, method="multileads")[selected_option_lead].tolist()
        else:
            if selected_option_rythm == "Fibrilacion auricular":
                heart_rythm = 'AFIB'
            elif selected_option_rythm == "Marcapasos":
                heart_rythm = 'PACE'
            else:
                heart_rythm = 'SVTAC'

            possible_indexes = (self.labels[(self.labels["Rythm"] == heart_rythm) & (self.labels["Lead"] == selected_option_lead)]).index.values
            index = possible_indexes[random.randint(0, len(possible_indexes) - 1)]
            X = self.signals[index]

            fs = 500
            self.X_filt_real = X
            self.X_filt_real = list(filtrar_señal(X, fs, moving_average=False))

            secs = int(len(self.X_filt_real) / fs)
            samps = secs * self.fs
            self.X_filt_real = list(scipy.signal.resample(self.X_filt_real, samps))

        self.ppg_signal_total = nk.ppg_simulate(duration=duration, sampling_rate=self.fs,
                                                heart_rate=self.heart_rate).tolist()

        self.resp_signal_total = nk.rsp_simulate(duration=duration, sampling_rate=self.fs,
                                                 respiratory_rate=self.resp_rate, method="breathmetrics").tolist()

        self.co2_signal_total = generate_co2_waveform(duration, self.fs, self.resp_rate, self.etco2)

    def _configure_plot(self, plot_widget, label_text, color_hex):
        plot_widget.setBackground('#000000')
        plot_widget.hideAxis('left')
        plot_widget.hideAxis('bottom')
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.hideButtons()
        plot_widget.setMenuEnabled(False)
        plot_widget.getViewBox().setDefaultPadding(0)

        text = pg.TextItem(label_text, color=color_hex, anchor=(0, 0))
        text.setFont(QFont("Arial", 14, QFont.Bold))
        plot_widget.addItem(text)
        text.setPos(0, 0)
        return text

    def plotSignal(self, dynamic_plot_widget, selected_option_rythm, selected_option_lead):

        self.generateSignals(selected_option_rythm, selected_option_lead)

        main_layout = QVBoxLayout(dynamic_plot_widget)

        # Top area: waveforms + parameters
        top_layout = QHBoxLayout()

        # --- Waveform area (65%) ---
        plot_area = QWidget()
        plot_layout = QVBoxLayout(plot_area)
        plot_layout.setSpacing(2)
        plot_layout.setContentsMargins(4, 4, 4, 4)

        self.ecg_plot = pg.PlotWidget()
        self.ppg_plot = pg.PlotWidget()
        self.resp_plot = pg.PlotWidget()
        self.co2_plot = pg.PlotWidget()

        self._ecg_label = self._configure_plot(self.ecg_plot, "ECG", "#00FF00")
        self._ppg_label = self._configure_plot(self.ppg_plot, "Pleth", "#00FFFF")
        self._resp_label = self._configure_plot(self.resp_plot, "RESP", "#FFFF00")
        self._co2_label = self._configure_plot(self.co2_plot, "CO2", "#FFFFFF")

        self.ecg_plot.setXRange(0, 5)
        self.ecg_plot.setYRange(-1, 1)
        self.ppg_plot.setXRange(0, 5)
        self.ppg_plot.setYRange(-2, 2)
        self.resp_plot.setXRange(0, 20)
        self.resp_plot.setYRange(-2, 2)
        self.co2_plot.setXRange(0, 20)
        self.co2_plot.setYRange(-5, self.etco2 + 10)

        plot_layout.addWidget(self.ecg_plot)
        plot_layout.addWidget(self.ppg_plot)
        plot_layout.addWidget(self.resp_plot)
        plot_layout.addWidget(self.co2_plot)

        # --- Parameter panel (35%) ---
        param_panel = QWidget()
        param_panel.setStyleSheet("background-color: #000000;")
        param_layout = QVBoxLayout(param_panel)
        param_layout.setSpacing(4)
        param_layout.setContentsMargins(4, 4, 4, 4)

        # Alarm indicator (hidden by default)
        self.alarm_indicator = QLabel("⚠ ALARMA ⚠")
        self.alarm_indicator.setFont(QFont("Arial", 18, QFont.Bold))
        self.alarm_indicator.setAlignment(QtCore.Qt.AlignCenter)
        self.alarm_indicator.setStyleSheet("color: #FF0000; background-color: #330000; border: 2px solid #FF0000; border-radius: 4px; padding: 4px;")
        self.alarm_indicator.setVisible(False)
        param_layout.addWidget(self.alarm_indicator)

        # HR
        hr_frame, self.hr_value_label = create_param_frame("HR", str(self.heart_rate), "bpm", "#00FF00")
        param_layout.addWidget(hr_frame)

        # SpO2
        spo2_frame, self.spo2_value_label = create_param_frame("SpO2", str(self.spo2), "%", "#00FFFF")
        param_layout.addWidget(spo2_frame)

        # FR
        fr_frame, self.fr_value_label = create_param_frame("FR", str(self.resp_rate), "rpm", "#FFFF00")
        param_layout.addWidget(fr_frame)

        # EtCO2
        etco2_frame, self.etco2_value_label = create_param_frame("EtCO2", str(self.etco2), "mmHg", "#FFFFFF")
        param_layout.addWidget(etco2_frame)

        # NIBP
        nibp_map = int(self.nibp_dia + (self.nibp_sys - self.nibp_dia) / 3)
        nibp_text = f"{self.nibp_sys}/{self.nibp_dia}"
        nibp_frame, self.nibp_value_label = create_param_frame("NIBP", nibp_text, f"({nibp_map}) mmHg", "#FF4444")
        param_layout.addWidget(nibp_frame)

        # Temp
        temp_frame, self.temp_value_label = create_param_frame("Temp", f"{self.temperature:.1f}", "°C", "#FF88CC")
        param_layout.addWidget(temp_frame)

        param_layout.addStretch()

        top_layout.addWidget(plot_area, stretch=65)
        top_layout.addWidget(param_panel, stretch=35)

        # --- Bottom toolbar ---
        toolbar = QWidget()
        toolbar.setStyleSheet("background-color: #0a0a14;")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)

        self.silence_button = QPushButton("Silenciar Alarma")
        self.silence_button.setFont(QFont("Arial", 13))
        self.silence_button.clicked.connect(self.silence_alarm)

        self.mute_button = QPushButton("Mute")
        self.mute_button.setFont(QFont("Arial", 13))
        self.mute_button.clicked.connect(self.toggle_mute)

        self.arrest_button = QPushButton("Paro Cardíaco")
        self.arrest_button.setFont(QFont("Arial", 13, QFont.Bold))
        self.arrest_button.clicked.connect(self.toggle_cardiac_arrest)

        self.return_button = QPushButton("Volver al Menú")
        self.return_button.setFont(QFont("Arial", 13))
        self.return_button.clicked.connect(self.return_to_main_screen)

        toolbar_layout.addWidget(self.silence_button)
        toolbar_layout.addWidget(self.mute_button)
        toolbar_layout.addWidget(self.arrest_button)
        toolbar_layout.addWidget(self.return_button)

        main_layout.addLayout(top_layout, stretch=1)
        main_layout.addWidget(toolbar)

        # --- Signal data buffers ---
        self.flag = False
        self.flag_resp = False
        self.time = [0]
        self.time_resp = [0]
        self.ECG_SIGNAL = [0]
        self.PPG_SIGNAL = [0]
        self.RESP_SIGNAL = [0]
        self.CO2_SIGNAL = [0]

        # Plot lines with monitor colors
        self.ecg_line = self.ecg_plot.plot(pen=pg.mkPen('#00FF00', width=2))
        self.ppg_line = self.ppg_plot.plot(pen=pg.mkPen('#00FFFF', width=2))
        self.resp_line = self.resp_plot.plot(pen=pg.mkPen('#FFFF00', width=2))
        self.co2_line = self.co2_plot.plot(pen=pg.mkPen('#FFFFFF', width=2))

        # Position text labels after range is set
        self._ecg_label.setPos(0.05, 0.9)
        self._ppg_label.setPos(0.05, 1.8)
        self._resp_label.setPos(0.1, 1.8)
        self._co2_label.setPos(0.1, self.etco2 + 8)

        # R-peak detection state
        self.last_beep_index = 0
        self.prev_ecg_value = 0.0
        self.ecg_threshold = 0.4
        self.refractory_samples = int(0.3 * self.fs)  # 300ms refractory
        self.samples_since_last_beep = self.refractory_samples

        # Silence countdown
        self.silence_remaining = 0

        # Start main timer
        self.timer = QtCore.QTimer()
        self.timer.setInterval(int(1 / self.fs * 1000))
        self.timer.timeout.connect(self.update_plot)
        self.timer.start()

        # Alarm flash timer
        self.alarm_flash_timer = QtCore.QTimer()
        self.alarm_flash_timer.setInterval(500)
        self.alarm_flash_timer.timeout.connect(self._toggle_alarm_flash)

        # Silence countdown timer
        self.silence_timer = QtCore.QTimer()
        self.silence_timer.setInterval(1000)
        self.silence_timer.timeout.connect(self._silence_countdown)

        # SpO2 decay timer (during cardiac arrest)
        self.spo2_decay_timer = QtCore.QTimer()
        self.spo2_decay_timer.setInterval(2000)
        self.spo2_decay_timer.timeout.connect(self._decay_spo2)

    def lead_place(self):
        self.leads_well_placed = not self.leads_well_placed

    def update_plot(self):
        array_aux = [np.nan]

        # ECG/PPG 5-second window scrolling
        if self.time[-1] > 5 and self.ECG_SIGNAL[-1] != 0 and not self.flag:
            self.flag = True
            self.time = array_aux + self.time
            self.ECG_SIGNAL = array_aux + self.ECG_SIGNAL
            self.PPG_SIGNAL = array_aux + self.PPG_SIGNAL

        if self.flag:
            self.time.append(self.time[0])
            self.time = self.time[1:]
            self.ECG_SIGNAL = self.ECG_SIGNAL[2:]
            self.ECG_SIGNAL = array_aux + self.ECG_SIGNAL
            self.PPG_SIGNAL = self.PPG_SIGNAL[2:]
            self.PPG_SIGNAL = array_aux + self.PPG_SIGNAL
        else:
            self.time.append(self.time[-1] + 1 / self.fs)

        # RESP/CO2 20-second window scrolling
        if self.time_resp[-1] > 20 and self.RESP_SIGNAL[-1] != 0 and not self.flag_resp:
            self.flag_resp = True
            self.time_resp = array_aux + self.time_resp
            self.RESP_SIGNAL = array_aux + self.RESP_SIGNAL
            self.CO2_SIGNAL = array_aux + self.CO2_SIGNAL

        if self.flag_resp:
            self.time_resp.append(self.time_resp[0])
            self.time_resp = self.time_resp[1:]
            self.RESP_SIGNAL = self.RESP_SIGNAL[2:]
            self.RESP_SIGNAL = array_aux + self.RESP_SIGNAL
            self.CO2_SIGNAL = self.CO2_SIGNAL[2:]
            self.CO2_SIGNAL = array_aux + self.CO2_SIGNAL
        else:
            self.time_resp.append(self.time_resp[-1] + 1 / self.fs)

        # Arduino serial read
        if self.arduino:
            try:
                if self.arduino.in_waiting:
                    signal_val = int(self.arduino.readline().decode().strip())
                    self.leads_well_placed = (signal_val == 1)
            except:
                pass

        # Signal index
        if not hasattr(self, 'signal_index'):
            self.signal_index = 0
        else:
            self.signal_index = (self.signal_index + 1) % len(self.X_filt_real)

        # Cardiac arrest: flatline all signals
        if self.cardiac_arrest_active:
            self.ECG_SIGNAL.append(0)
            self.PPG_SIGNAL.append(0)
            self.RESP_SIGNAL.append(0)
            self.CO2_SIGNAL.append(0)
        else:
            # Normal signal
            if self.leads_well_placed:
                ecg_val = self.X_filt_real[self.signal_index]
                self.ECG_SIGNAL.append(ecg_val)

                # R-peak detection for heartbeat beep
                self.samples_since_last_beep += 1
                if (self.prev_ecg_value < self.ecg_threshold <= ecg_val and
                        self.samples_since_last_beep >= self.refractory_samples):
                    self.samples_since_last_beep = 0
                    self.audio_manager.play_heartbeat_beep(self.spo2)
                self.prev_ecg_value = ecg_val
            else:
                self.ECG_SIGNAL.append(0)

            self.PPG_SIGNAL.append(self.ppg_signal_total[self.signal_index])
            self.RESP_SIGNAL.append(self.resp_signal_total[self.signal_index])
            self.CO2_SIGNAL.append(self.co2_signal_total[self.signal_index])

        # Update plot lines
        for line, sig in [(self.ecg_line, self.ECG_SIGNAL),
                          (self.ppg_line, self.PPG_SIGNAL)]:
            line.setData(self.time, sig)

        self.resp_line.setData(self.time_resp, self.RESP_SIGNAL)
        self.co2_line.setData(self.time_resp, self.CO2_SIGNAL)

        # Update numeric displays
        if self.cardiac_arrest_active:
            # HR flashes between "0" and "" via alarm flash timer
            spo2_display = str(int(self.spo2_decay_value))
            self.spo2_value_label.setText(spo2_display)
            self.fr_value_label.setText("0")
            self.etco2_value_label.setText("0")
        else:
            self.hr_value_label.setText(str(self.heart_rate))
            self.hr_value_label.setStyleSheet("color: #00FF00; border: none;")
            self.spo2_value_label.setText(str(self.spo2))
            self.fr_value_label.setText(str(self.resp_rate))
            self.etco2_value_label.setText(str(self.etco2))

    # --- Cardiac Arrest ---

    def toggle_cardiac_arrest(self):
        self.cardiac_arrest_active = not self.cardiac_arrest_active

        if self.cardiac_arrest_active:
            self.arrest_button.setStyleSheet("""
                QPushButton {
                    background-color: #CC0000;
                    color: #FFFFFF;
                    border: 2px solid #FF0000;
                    border-radius: 4px;
                    padding: 8px 16px;
                    font-size: 14px;
                }
            """)
            self.alarm_indicator.setVisible(True)
            self.alarm_flash_timer.start()
            self.spo2_decay_timer.start()
            self.spo2_decay_value = float(self.spo2)
            self.audio_manager.start_flatline_alarm()
            self.hr_value_label.setText("0")
        else:
            self.arrest_button.setStyleSheet("")
            self.alarm_indicator.setVisible(False)
            self.alarm_flash_timer.stop()
            self.spo2_decay_timer.stop()
            self.spo2_decay_value = float(self.spo2)
            self.audio_manager.stop_alarm()
            self.hr_value_label.setStyleSheet("color: #00FF00; border: none;")

    def _toggle_alarm_flash(self):
        self.alarm_flash_visible = not self.alarm_flash_visible
        if self.alarm_flash_visible:
            self.alarm_indicator.setStyleSheet("color: #FF0000; background-color: #660000; border: 2px solid #FF0000; border-radius: 4px; padding: 4px;")
            self.hr_value_label.setStyleSheet("color: #FF0000; border: none;")
            self.hr_value_label.setText("0")
        else:
            self.alarm_indicator.setStyleSheet("color: #FF0000; background-color: #330000; border: 2px solid #FF0000; border-radius: 4px; padding: 4px;")
            self.hr_value_label.setStyleSheet("color: #330000; border: none;")
            self.hr_value_label.setText("0")

    def _decay_spo2(self):
        if self.cardiac_arrest_active and self.spo2_decay_value > 0:
            self.spo2_decay_value = max(0, self.spo2_decay_value - 1)

    # --- Audio controls ---

    def toggle_mute(self):
        is_muted = self.audio_manager.toggle_mute()
        self.mute_button.setText("Unmute" if is_muted else "Mute")

    def silence_alarm(self):
        duration = self.audio_manager.silence_alarms(120)
        self.silence_remaining = duration
        self.silence_button.setText(f"Silenciado ({self.silence_remaining}s)")
        self.silence_timer.start()

    def _silence_countdown(self):
        self.silence_remaining -= 1
        if self.silence_remaining <= 0:
            self.silence_timer.stop()
            self.audio_manager.alarm_silenced = False
            self.silence_button.setText("Silenciar Alarma")
        else:
            self.silence_button.setText(f"Silenciado ({self.silence_remaining}s)")

    # --- Navigation ---

    def return_to_main_screen(self):
        self.timer.stop()
        self.alarm_flash_timer.stop()
        self.silence_timer.stop()
        self.spo2_decay_timer.stop()
        self.audio_manager.stop_alarm()
        self.audio_manager.alarm_silenced = False
        self.audio_manager.muted = False
        self.cardiac_arrest_active = False
        self.stacked_widget.removeWidget(self.stacked_widget.currentWidget())
        self.stacked_widget.setCurrentWidget(self.main_screen_widget)

    def __del__(self):
        if hasattr(self, 'audio_manager'):
            self.audio_manager.stop_alarm()
        if hasattr(self, 'arduino') and self.arduino:
            self.arduino.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
