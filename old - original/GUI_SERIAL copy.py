import sys
from PyQt5 import QtCore
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QStackedWidget, QLabel, QComboBox, QPushButton, QHBoxLayout, QLineEdit, QRadioButton
import pyqtgraph as pg
import numpy as np
import utils as u
import scipy
import pandas as pd
import csv
import random
import serial
import neurokit2 as nk


def filtrar_señal(señal, fs, correct_signal = False, moving_average = False, asd = True):
    if asd ==False:
        return señal
    
    elif correct_signal:
        return u.integrador(u.correct_signal(u.pasabanda(u.med_filt(señal, fs), fs= fs, lowcut= 0.5, highcut= 50),fs),fs, largo_ventana= 0.01)
    else:

        if moving_average:
            return u.integrador(u.pasabanda(u.med_filt(señal, fs), fs= fs, lowcut= 0.5, highcut= 50),fs, largo_ventana= 0.05)
        else:
            return u.pasabanda(u.med_filt(señal, fs), fs= fs, lowcut= 0.5, highcut= 50)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Set window properties
        self.setGeometry(200, 200, 2800, 1500)  # Set the size of the window
        self.setWindowTitle("ECG Simulator")

        # Add serial port setup
        try:
            self.arduino = serial.Serial('COM3', 9600)  # Adjust port as needed
        except:
            print("Could not connect to Arduino")
            self.arduino = None
        
        #load signals
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

        self.labels = pd.DataFrame(labels, columns = ["Rythm","Index","Lead"])

        # Add parameters for simulation
        self.heart_rate = 60
        self.spo2 = 98
        self.resp_rate = 12

        # Set up the main screen
        self.main_screen_widget = QWidget()
        self.setup_main_screen()

        # Set up the stacked widget to switch between screens
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self.main_screen_widget)

        # Set the central widget to the stacked widget
        self.setCentralWidget(self.stacked_widget)

    def setup_main_screen(self):
        # Create widgets for the main screen
        title_label = QLabel("Simulador de ECG", self)
        title_label.setFont(QFont("Arial", 20, QFont.Bold))


        dropdown_rythm_label = QLabel("Ritmo:", self)
        dropdown_rythm_label.setFont((QFont("Arial", 14)))
        dropdown_lead_label = QLabel("Derivación:", self)
        dropdown_lead_label.setFont((QFont("Arial", 14)))
        
        dropdown_rythm = QComboBox(self)
        dropdown_rythm.setObjectName("dropdown_rythm")
        dropdown_rythm.setFont(QFont("Arial", 14))       
        dropdown_rythm.addItems(["Ritmo Sinusal", "Fibrilacion auricular", "Marcapasos", "Arritmia Supraventricular"])

        dropdown_lead = QComboBox(self)
        dropdown_lead.setObjectName("dropdown_lead")
        dropdown_lead.setFont(QFont("Arial", 14))    
        dropdown_lead.addItems(["I","II","III","aVF","aVR","aVL","V1","V2","V3","V4","V5","V6"])

#        input_text_box = QLineEdit(self)  # Add a text box for user input
#        input_text_box.setPlaceholderText("")
        
        """
        radio_button_label = QLabel("Requerir cables bien conectados?", self)
        radio_button_label.setFont(QFont("Arial", 14))
        radio_btn1 = QRadioButton('Si', self)
        radio_btn1.setFont(QFont("Arial", 14)) 
        radio_btn2 = QRadioButton('No', self)
        radio_btn2.setFont(QFont("Arial", 14))

        # Create a layout
        hbox_radio_button = QHBoxLayout()
        hbox_radio_button.addWidget(radio_button_label)
        hbox_radio_button.addWidget(radio_btn1)
        hbox_radio_button.addWidget(radio_btn2)
        """

        start_button = QPushButton("Comenzar", self)
        start_button.setFont(QFont("Arial", 14))
        start_button.clicked.connect(self.start_simulation)
        

        # Layout for the main screen
        layout = QVBoxLayout(self.main_screen_widget)
        dropdown_label_layout = QHBoxLayout()
        dropdown_layout = QHBoxLayout()
        

#        dropdown_rythm_label.setContentsMargins(0, 0, 0, 0)
#        dropdown_lead_label.setContentsMargins(0, 0, 0, 0)

        # Add labels and dropdowns to the sub-layout
        dropdown_label_layout.addWidget(dropdown_rythm_label, alignment= QtCore.Qt.AlignHCenter)
        dropdown_layout.addWidget(dropdown_rythm)
        dropdown_label_layout.addWidget(dropdown_lead_label, alignment= QtCore.Qt.AlignHCenter)
        dropdown_layout.addWidget(dropdown_lead)


        # Set alignment for labels
        #dropdown_rythm_label.setAlignment(QtCore.Qt.AlignHCenter)
        #dropdown_lead_label.setAlignment(QtCore.Qt.AlignHCenter)

        # Add parameter controls
        params_label = QLabel("Parámetros:", self)
        params_label.setFont(QFont("Arial", 14))

        # Heart rate input
        hr_label = QLabel("Frecuencia cardíaca (bpm):", self)
        hr_label.setFont(QFont("Arial", 12))
        self.hr_input = QLineEdit(str(self.heart_rate), self)
        self.hr_input.setFont(QFont("Arial", 12))

        # SpO2 input
        spo2_label = QLabel("SpO2 (%):", self)
        spo2_label.setFont(QFont("Arial", 12))
        self.spo2_input = QLineEdit(str(self.spo2), self)
        self.spo2_input.setFont(QFont("Arial", 12))

        # Respiratory rate input
        rr_label = QLabel("Frecuencia respiratoria (rpm):", self)
        rr_label.setFont(QFont("Arial", 12))
        self.rr_input = QLineEdit(str(self.resp_rate), self)
        self.rr_input.setFont(QFont("Arial", 12))

        # Create parameter input layout
        params_layout = QVBoxLayout()
        params_layout.addWidget(params_label)
        params_layout.addWidget(hr_label)
        params_layout.addWidget(self.hr_input)
        params_layout.addWidget(spo2_label)
        params_layout.addWidget(self.spo2_input)
        params_layout.addWidget(rr_label)
        params_layout.addWidget(self.rr_input)

        # Add to main layout
        layout.addWidget(title_label, alignment= QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)  # Title at the top, centered horizontally
        layout.addStretch(1)
        layout.addLayout(dropdown_label_layout)
        layout.setSpacing(0)
        layout.addLayout(dropdown_layout)
        layout.addLayout(params_layout)
        
        
        layout.addStretch(1)# Add stretch to push the next widgets to the bottom
        layout.addWidget(start_button, alignment= QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter)  # Start button at the bottom, centered horizontally


    def start_simulation(self):
        # Get the selected option from the dropdown
        selected_option_rythm = self.main_screen_widget.findChild(QComboBox, "dropdown_rythm").currentText()
        selected_option_lead = self.main_screen_widget.findChild(QComboBox, "dropdown_lead").currentText()
        
        # Only use signal_index for non-sinus rhythms
        signal_index = 10 if selected_option_rythm != "Ritmo Sinusal" else None
        
        # Create widgets for the dynamic plot screen
        dynamic_plot_widget = QWidget()
        self.leads_well_placed = True
        self.plotSignal(dynamic_plot_widget, selected_option_rythm, selected_option_lead, signal_index)

        # Add the dynamic plot screen to the stacked widget and switch to it
        self.stacked_widget.addWidget(dynamic_plot_widget)
        self.stacked_widget.setCurrentWidget(dynamic_plot_widget)

    def on_finished(self):
        self.timer.stop()
        self.plot_widget.clear()

    def plotSignal(self, dynamic_plot_widget, selected_option_rythm, selected_option_lead, signal_index):
        # Create layout with plots and parameter display
        main_layout = QHBoxLayout(dynamic_plot_widget)
        
        # Plot area
        plot_area = QWidget()
        plot_layout = QVBoxLayout(plot_area)
        
        # Create three plot widgets
        self.ecg_plot = pg.PlotWidget()
        self.ppg_plot = pg.PlotWidget()
        self.resp_plot = pg.PlotWidget()
        
        # Configure plots
        plots = [(self.ecg_plot, "ECG"), (self.ppg_plot, "PPG"), (self.resp_plot, "Respiración")]
        for plot, title in plots:
            plot.setTitle(title, color="b", size="15pt")
            plot.setLabel("left", "Amplitude")
            plot.setLabel("bottom", "Tiempo (s)")
            plot.showGrid(x=True, y=True)
            plot.setXRange(0, 5)
            plot_layout.addWidget(plot)

        # Parameter display panel
        param_panel = QWidget()
        param_layout = QVBoxLayout(param_panel)
        param_layout.setAlignment(QtCore.Qt.AlignTop)
        
        # Create parameter displays
        self.hr_display = QLabel(f"FC: {self.heart_rate} bpm")
        self.spo2_display = QLabel(f"SpO2: {self.spo2}%")
        self.rr_display = QLabel(f"FR: {self.resp_rate} rpm")
        
        for label in [self.hr_display, self.spo2_display, self.rr_display]:
            label.setFont(QFont("Arial", 16, QFont.Bold))
            param_layout.addWidget(label)

        # Add control buttons
        return_button = QPushButton("Volver a menu principal")
        return_button.clicked.connect(self.return_to_main_screen)
        param_layout.addWidget(return_button)

        # Add layouts to main layout
        main_layout.addWidget(plot_area, stretch=4)
        main_layout.addWidget(param_panel, stretch=1)

        # Generate signals with rhythm selection
        self.generate_signals(selected_option_rythm)
        
        # Set up plot data with initial values
        self.time = [0]
        self.ecg_data = [0]
        self.ppg_data = [0]
        self.resp_data = [0]
        
        # Create plot lines
        self.ecg_line = self.ecg_plot.plot(pen='r')
        self.ppg_line = self.ppg_plot.plot(pen='b')
        self.resp_line = self.resp_plot.plot(pen='g')
        
        # Initialize signal index
        self.current_index = 0
        
        # Start timer for updates
        self.timer = QtCore.QTimer()
        self.timer.setInterval(int(1000/self.fs))
        self.timer.timeout.connect(self.update_plot)
        self.timer.start()

    def generate_signals(self, selected_option_rythm):
        # Get parameters from inputs
        self.heart_rate = int(self.hr_input.text())
        self.spo2 = int(self.spo2_input.text())
        self.resp_rate = int(self.rr_input.text())
        
        # Generate signals using neurokit2 for sinus rhythm
        duration = 60  # increased duration to 60 seconds
        self.fs = 150  # sampling rate
        
        if selected_option_rythm == "Ritmo Sinusal":
            # Generate signals using neurokit2
            self.ecg_signal = nk.ecg_simulate(duration=duration, sampling_rate=self.fs, 
                                            heart_rate=self.heart_rate)
            self.ppg_signal = nk.ppg_simulate(duration=duration, sampling_rate=self.fs, 
                                            heart_rate=self.heart_rate)
            self.resp_signal = nk.rsp_simulate(duration=duration, sampling_rate=self.fs, 
                                             respiratory_rate=self.resp_rate)
        else:
            # Use pre-generated signals from files
            signal_index = self.labels[
                (self.labels['Rythm'] == selected_option_rythm) & 
                (self.labels['Lead'] == selected_option_lead)
            ].index[0]
            
            # Get the corresponding signal and repeat it to match duration
            base_signal = self.signals[signal_index]
            repeats = int(np.ceil((duration * self.fs) / len(base_signal)))
            self.ecg_signal = np.tile(base_signal, repeats)[:int(duration * self.fs)]
            
            # Generate PPG and resp signals with neurokit (or you could load from files if available)
            self.ppg_signal = nk.ppg_simulate(duration=duration, sampling_rate=self.fs, 
                                            heart_rate=self.heart_rate)
            self.resp_signal = nk.rsp_simulate(duration=duration, sampling_rate=self.fs, 
                                             respiratory_rate=self.resp_rate)

    def update_plot(self):
        # Add new time point
        new_time = self.time[-1] + 1/self.fs if self.time else 0
        
        # Reset if we've reached the end of the signal or passed 5 seconds
        if new_time > 5:
            self.time = self.time[1:]  # Remove oldest time point
            self.ecg_data = self.ecg_data[1:]  # Remove oldest data point
            self.ppg_data = self.ppg_data[1:]
            self.resp_data = self.resp_data[1:]
        
        self.time.append(new_time)
        
        # Add new data points
        self.ecg_data.append(self.ecg_signal[self.current_index])
        self.ppg_data.append(self.ppg_signal[self.current_index])
        self.resp_data.append(self.resp_signal[self.current_index])
        
        # Update current index and wrap around if needed
        self.current_index = (self.current_index + 1) % len(self.ecg_signal)
        
        # Update plots
        self.ecg_line.setData(self.time, self.ecg_data)
        self.ppg_line.setData(self.time, self.ppg_data)
        self.resp_line.setData(self.time, self.resp_data)
        
        # Update parameter displays
        self.hr_display.setText(f"FC: {self.heart_rate} bpm")
        self.spo2_display.setText(f"SpO2: {self.spo2}%")
        self.rr_display.setText(f"FR: {self.resp_rate} rpm")

    def return_to_main_screen(self):
        # Remove the dynamic plot screen from the stacked widget and switch to the main screen
        self.timer.stop()
        self.stacked_widget.removeWidget(self.stacked_widget.currentWidget())
        self.stacked_widget.setCurrentWidget(self.main_screen_widget)

    def __del__(self):
        # Close serial port when application exits
        if hasattr(self, 'arduino') and self.arduino:
            self.arduino.close()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
