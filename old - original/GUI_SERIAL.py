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

        # Add title, dropdowns, and button to the main layout
        layout.addWidget(title_label, alignment= QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)  # Title at the top, centered horizontally
        layout.addStretch(1)
        layout.addLayout(dropdown_label_layout)
        layout.setSpacing(0)
        layout.addLayout(dropdown_layout)
        #layout.addStretch(1)
        #layout.addLayout(hbox_radio_button)
        
        
        layout.addStretch(1)# Add stretch to push the next widgets to the bottom
        layout.addWidget(start_button, alignment= QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter)  # Start button at the bottom, centered horizontally


    def start_simulation(self):
        # Get the selected option from the dropdown
        selected_option_rythm = self.main_screen_widget.findChild(QComboBox, "dropdown_rythm").currentText()
        selected_option_lead = self.main_screen_widget.findChild(QComboBox, "dropdown_lead").currentText()
        #signal_index = int(self.main_screen_widget.findChild(QLineEdit).text())
        signal_index = 10
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
        
        return_button = QPushButton("Volver a menu principal", dynamic_plot_widget)
        return_button.clicked.connect(self.return_to_main_screen)

        leads_well_placed_button = QPushButton("Cambiar leads bien o mal", dynamic_plot_widget)
        leads_well_placed_button.clicked.connect(self.lead_place)

        plot_widget = pg.PlotWidget()
        layout = QVBoxLayout(dynamic_plot_widget)
        layout.setContentsMargins(50, 10, 10, 10)
        layout.addWidget(plot_widget)
        layout.addWidget(return_button)
        layout.addWidget(leads_well_placed_button)

            
        #Select rythm 
        #signal_index = 10
        if selected_option_rythm == "Ritmo Sinusal":
            heart_rythm = 'SR'

        elif selected_option_rythm == "Fibrilacion auricular":
            heart_rythm = 'AFIB'

        elif selected_option_rythm == "Marcapasos":
            heart_rythm = 'PACE'
 
        else:
            heart_rythm = 'SVTAC'

        
        #Select lead (old)
        if selected_option_lead == "I":
            lead_index = 0

        elif selected_option_lead == "II":
            lead_index = 1

        elif selected_option_lead == "III":
            lead_index = 2

        elif selected_option_lead == "aVF":
            lead_index = 3 

        elif selected_option_lead == "aVR":
            lead_index = 4

        elif selected_option_lead == "aVL":
            lead_index = 5

        elif selected_option_lead == "V1":
            lead_index = 6

        elif selected_option_lead == "V2":
            lead_index = 7
        
        elif selected_option_lead == "V3":
            lead_index = 8
        
        elif selected_option_lead == "V4":
            lead_index = 9
        
        elif selected_option_lead == "V5":
            lead_index = 10

        else:
            lead_index = 11
        
        #X = self.dict_by_rythm[heart_rythm]
        possible_indexes = (self.labels[(self.labels["Rythm"] == heart_rythm) & (self.labels["Lead"] == selected_option_lead)]).index.values
        index = possible_indexes[random.randint(0,len(possible_indexes)-1)]
        X = self.signals[index]

        self.fs = 500
        self.X_filt_real = X
        self.X_filt_real = list(filtrar_señal(X,self.fs,moving_average=False))
        #GET SIGNAL FROM WHOLE DATABASE
        #self.X_filt_real = list(filtrar_señal(X[signal_index][:,lead_index],self.fs,moving_average=True))
        
        secs = int(len(self.X_filt_real)/self.fs)# Number of seconds in signal X
        self.fs = 150
        samps = secs*150     # Number of samples to downsample
        self.X_filt_real = list(scipy.signal.resample(self.X_filt_real, samps))
        #y = np.arange(0,len(X_filt_real)/fs,1/fs)
        
        # --------------------------------------------------------------
        
        self.flag = False
        pen = pg.mkPen(color=(255, 0, 0), width=2)
        plot_widget.setTitle("ECG", color="b", size="15pt")
        styles = {"color": "red", "font-size": "18px"}
        plot_widget.setLabel("left", "Amplitude", **styles)
        plot_widget.setLabel("bottom", "S (min)", **styles)
        plot_widget.addLegend()
        plot_widget.showGrid(x=True, y=True)
        plot_widget.setXRange(0, 5)
        plot_widget.setYRange(-1, 1)
#        self.time = list(np.arange(0, 5, 1/self.fs))
#        self.ECG_SIGNAL = list(np.zeros(5*self.fs))
        self.time = [0]
        self.ECG_SIGNAL = [0]
        # Get a line reference
        self.line = plot_widget.plot(
            self.time,
            self.ECG_SIGNAL,
#            name="ECG PLOT",
            pen=pen,
#            symbol="+",
#            symbolSize=15,
#            symbolBrush="b",
        )

        self.timer = QtCore.QTimer()
        self.timer.setInterval(int(1/self.fs*1000))
        self.timer.timeout.connect(self.update_plot)
        self.timer.start()
        
    def lead_place(self):
        if (self.leads_well_placed):
            self.leads_well_placed = False
        else:
            self.leads_well_placed = True

    def update_plot(self):
        array_aux = [np.nan]
        if self.time[-1] > 5 and self.ECG_SIGNAL[-1] != 0 and not self.flag:
            self.flag = True
            self.time = array_aux + self.time
            self.ECG_SIGNAL = array_aux + self.ECG_SIGNAL
            
        if self.flag:
            self.time.append(self.time[0])
            self.time = self.time[1:]
            self.ECG_SIGNAL = self.ECG_SIGNAL[2:]
            self.ECG_SIGNAL = array_aux + self.ECG_SIGNAL     
        else:
            self.time.append(self.time[-1] + 1/self.fs)
        
        # Read Arduino signal if available
        if self.arduino:
            try:
                if self.arduino.in_waiting:
                    signal = int(self.arduino.readline().decode().strip())
                    self.leads_well_placed = (signal == 1)
            except:
                pass
            
        if (self.leads_well_placed):
            self.ECG_SIGNAL.append(self.X_filt_real[0])
        else:
            self.ECG_SIGNAL.append(0)
            
        self.line.setData(self.time, self.ECG_SIGNAL)
        self.X_filt_real.append(self.X_filt_real[0])
        self.X_filt_real = self.X_filt_real[1:]

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
