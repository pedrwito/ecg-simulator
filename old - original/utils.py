import scipy
import numpy as np
from scipy import signal
import matplotlib.pyplot as plt

def pasabanda(signal, fs, lowcut = 8, highcut = 25):
  signal_ = signal.copy()
  order1 = 5
  b, a = scipy.signal.butter(order1, [lowcut, highcut], btype='band', analog=False, fs=fs)
  signal1 = scipy.signal.filtfilt(b, a, signal_)

  return signal1

def filtrar_artefactos(signal, fs, k_h_max = 2.5):
  signal_ = signal.copy()
  peaks, _  = scipy.signal.find_peaks(signal_, height = np.mean(signal), distance = fs*0.24)
  h_median = np.median(signal_[peaks])
  h_mean = np.mean(signal_[peaks])

  n_ventana = int(0.2*fs)
  k = int((len(signal_) - n_ventana)/(0.1*fs))
  for i in range(k):
    start = int(0.1*i*fs)
    ventana = signal[start:(start+n_ventana)]
    max = np.max(ventana)
    if (max > k_h_max*h_median) and (max > k_h_max*h_mean):
      signal_[start:(start+n_ventana)] = 0
  
  return signal_

def derivador(signal, fs):
  signal_ = signal.copy()
  L = 1
  h = np.zeros(2*L + 1)
  h[0] = 1
  h[-1] = -1
  h = h*fs / (2*L)
  signal_ = np.convolve(signal_, h, 'same')
  return signal_

def rectificador(signal, fs):
  signal_ = signal.copy()
  N = len(signal_)
  signal2 = np.zeros(N)
  for i in range(0,N):
    signal2[i] = signal_[i]**2
  return signal2

def integrador(signal, fs, largo_ventana = 0.150):
  signal_ = signal.copy()
  ventana = round(largo_ventana*fs)
  x = np.ones(ventana)/ventana
  signal2 = np.convolve(signal_, x,'same')
  return signal2

def PanTompkins(signal, fs, k_h_max_filt_art = 2.5, filt_art = True):
  signal_ = signal.copy()

  # Pasabanda
  signal1 = pasabanda(signal_, fs)

  # Derivador
  signal2 = derivador(signal1, fs)
  
  # Rectificador
  signal3 = rectificador(signal2, fs)

  # Filtrado de artefactos
  if filt_art:
    signal3 = filtrar_artefactos(signal3, fs, k_h_max = k_h_max_filt_art)
  
  # Integrador
  signal4 = integrador(signal3, fs)

  return signal4

def med_filt(signal_, fs):
    med200 = signal.medfilt(np.array(signal_), [int(fs/5 + 1)])
    med600 = signal.medfilt(np.array(med200), [int(3*fs/5 + 1)])
    return np.subtract(signal_, med600)

def round_prob(prob, threshold):
  if prob >= threshold:
    result = 1
  else:
    result = 0
  return result

def R_peaks(signal_, fs, k_h_max_R = 1, k_h_max_filt_art = 2.5, filt_art = True, PyT = True):
  if PyT:
    signal_f = PanTompkins(signal_, fs, k_h_max_filt_art = k_h_max_filt_art, filt_art = filt_art)
  else:
    signal_f = signal_
  h_max = k_h_max_R*np.mean(signal_f)
  peaks, _  = signal.find_peaks(signal_f, height = h_max, distance = round(fs*0.24))
  peaks_ok = []
  k = int(0.05*fs)
  for peak in peaks:
    start = (peak - k)
    if start < 0:
       start = 0
    QRS = np.abs(signal_[start:start+2*k])
    c = np.argmax(QRS)
    peak_ok = start+c
    peaks_ok.append(peak_ok)
  return np.array(peaks_ok)

def get_templates(signal_, fs):
  i_peaks = R_peaks(signal_, fs)
  i_before = int(0.2*fs)
  i_after = int(0.4*fs)
  templates = []
  for i in i_peaks:
      template = signal_[(i-i_before):(i+i_after)]
      if len(template) == int(0.6*fs):
          templates.append(template)
  return np.array(templates)

def check_polarity(signal_, templates, positive_polarity = True):
  templates_min = np.min(np.median(templates, axis=0))
  templates_max = np.max(np.median(templates, axis=0))
  if np.abs(templates_min) > np.abs(templates_max):
      if positive_polarity:
        signal_ = -1*signal_
        templates = -1*templates
  return signal_, templates

def normalize_amplitude(signal_, templates):
  templates_max = np.max(np.median(templates, axis=0))
  return signal_ / templates_max

def correct_signal(signal_, fs):
  templates = get_templates(signal_, fs)
  signal_, templates = check_polarity(signal_, templates)
  signal_ = normalize_amplitude(signal_, templates)
  return signal_

def plot_R(senal, fs, r_peaks = None, r_peaks_ect = None, AV_blocks = None, filt_art = True, labels = False):
  train_i = np.array(senal)
  if r_peaks == None:
    r_peaks = R_peaks(senal, fs, filt_art=filt_art)
  
  t = np.linspace(0, len(train_i)/ fs, len(train_i))
  dur = int(len(train_i) / fs)
  f_size = int(dur * 20/30)
  t_ticks = np.arange(0,dur+1,1)

  t_R = t[r_peaks]
  R = train_i[r_peaks]
  n = np.arange(len(t_R))

  plt.figure(figsize = [f_size/1.5,5/1.5])
  plt.plot(t, train_i, label = 'Señal')
  plt.plot(t_R, R, 'oy', label = 'No ectópicos')
  plt.xlabel('Tiempo [s]')
  plt.xlim([0, dur])
  plt.xticks(t_ticks)
  plt.ylabel('Amplitud')
  # plt.title('Train ' + str(i) + ' con detector QRS')
  plt.grid(visible=None, which='major', axis='both')
  #for i, txt in enumerate(n):
      #plt.annotate(txt, (t_R[i], R[i]))

  if r_peaks_ect != None:
     if len(r_peaks_ect) > 0:
      t_ect = t[r_peaks_ect]
      ects = train_i[r_peaks_ect]
      plt.plot(t_ect, ects, 'or', label = 'Ectópicos')

  if AV_blocks != None:
     if len(AV_blocks) > 0:
      t_blocks = t[AV_blocks]
      blocks = -0.1*np.ones(len(AV_blocks))
      plt.plot(t_blocks, blocks, 'or', label = 'Bloqueos AV')

  if labels:
     plt.legend(loc = 'lower right')
  plt.show()

  return

def pasabajos(signal, fs, cut = 40):
    signal_ = signal.copy()
    order = 5
    b, a = scipy.signal.butter(order, cut, btype='lowpass', analog=False, fs= fs)
    signal1 = scipy.signal.filtfilt(b, a, signal_)
    return signal1

def pasaaltos(signal,fs):
    signal_ = signal.copy()
    cut = 0.7
    order = 2
    b, a = scipy.signal.butter(order, cut, btype='highpass', analog=False, fs=fs)
    signal1 = scipy.signal.filtfilt(b, a, signal_)
    return signal1

#--------------------------SERIES RR-------------------------------------------------------------------------------

def serie_RR(segment, fs, k_h_max_R = 1, k_h_max_filt_art = 2.5, filt_art = True):
    serie = []
    start = 0
    r_peaks = R_peaks(segment, fs, k_h_max_R = k_h_max_R, k_h_max_filt_art = k_h_max_filt_art, filt_art = filt_art)
    t = np.linspace(0, len(segment)/fs, len(segment))
    
    for j in range(len(r_peaks)):
        i_r = r_peaks[j]
        t_r = t[i_r]
        if j == 0:
          start = i_r
        else:
          t_r_prev = t[r_peaks[j-1]]
          serie.append(t_r - t_r_prev)

    return serie, start, r_peaks

def process_serie_RR_1(serie_, r_peaks = [], int_min = 0.24, int_max = 2):
    serie = serie_.copy()
    serie_aux = []
    i_del = []
    if len(r_peaks) > 0:
      r_peaks_aux = [r_peaks[0]]
    n_mini = 0
    for i in range(len(serie)):
        interval = serie[i]
        if len(r_peaks) > 0:
          r_peak = r_peaks[i+1]
        if interval < int_min:
          if 0 < i < (len(serie) - 1):
            n_mini = n_mini + 1
            interval_prev = serie[i-1]
            interval_next = serie[i+1]
            if interval_prev < interval_next:
              if len(serie_aux) > 0:
                serie_aux[-1] = interval_prev + interval
                if len(r_peaks) > 0:
                  r_peaks_aux[-1] = r_peak
              else:
                n_mini = n_mini - 1
                serie_aux.append(interval_prev + interval)
                if len(r_peaks) > 0:
                  r_peaks_aux.append(r_peak)
            else:
                serie[i+1] = interval_next + interval
        elif interval > int_max:
          i_del.append(i-n_mini)
        else:
          serie_aux.append(interval)
          if len(r_peaks) > 0:
            r_peaks_aux.append(r_peak)
    return serie_aux, r_peaks_aux, i_del

def process_serie_RR_2(serie_, r_peaks, int_med, i_del = [], k_min = 1/3, k_max = 3):
    serie = serie_.copy()
    serie_final = []
    r_peaks_final = []
    if len(r_peaks) > 0:
      r_peaks_final.append(r_peaks[0])
    n_mini = 0
    for i in range(len(serie)):
      interval = serie[i]
      if len(r_peaks) > 0:
        r_peak = r_peaks[i+1]
      if interval < (k_min*int_med):
        if 0 < i < (len(serie) - 1):
          n_mini = n_mini + 1
          for j in range(len(i_del)):
            if i < i_del[j]:
              i_del[j] = i_del[j] - 1
          interval_prev = serie[i-1]
          interval_next = serie[i+1]
          if interval_prev < interval_next:
            if len(serie_final) > 0:
                serie_final[-1] = interval_prev + interval
                if len(r_peaks) > 0:
                  r_peaks_final[-1] = r_peak
            else:
                n_mini = n_mini - 1
                serie_final.append(interval_prev + interval)
                if len(r_peaks) > 0:
                  r_peaks_final.append(r_peak)
          else:
            serie[i+1] = interval_next + interval
      elif interval > (k_max*int_med):
          i_del.append(i-n_mini)
      else:
          serie_final.append(interval)
          if len(r_peaks) > 0:
            r_peaks_final.append(r_peaks[i+1])
    i_del = np.array(i_del)
    return serie_final, r_peaks_final, i_del

def get_serie_RR(segments, fs, process_med = True):
  serie = []
  serie_en_partes_ = []
  r_peaks_en_partes_ = []
  for segment in segments:
      serie_seg, start, r_peaks_seg = serie_RR(segment, fs)
      if len(serie_seg) > 2:
          serie_seg, r_peaks_seg, i_del = process_serie_RR_1(serie_seg, r_peaks_seg)
      serie = serie + serie_seg
      serie_en_partes_.append(serie_seg)
      r_peaks_en_partes_.append(r_peaks_seg)
  
  if process_med:
    int_med = np.median(serie)
    serie = []
    serie_en_partes = []
    r_peaks_en_partes = []
    for i in range(len(serie_en_partes_)):
        serie_seg = serie_en_partes_[i]
        r_peaks_seg = r_peaks_en_partes_[i]
        if len(serie_seg) > 2:
            serie_seg, r_peaks_seg, i_del = process_serie_RR_2(serie_seg, r_peaks_seg, int_med)
        serie = serie + serie_seg
        serie_en_partes.append(serie_seg)
        r_peaks_en_partes.append(r_peaks_seg)
  else:
    serie_en_partes = serie_en_partes_
    r_peaks_en_partes = r_peaks_en_partes_

  return serie, serie_en_partes, r_peaks_en_partes

#--------------------------AF INDICES-------------------------------------------------------------------------------

def P_NMASD(serie_):
  serie = np.array(serie_)
  dx = np.diff(serie)
  P = np.sum(np.abs(dx)) / np.mean(serie)
  return P

def poincare2(serie_, l_bin = 0.125, correct = True):
    serie = np.array(serie_)
    dx = np.diff(serie)
    N = len(dx)
    poin = np.zeros([2,N-1])
    poin[0,:] = dx[:-1]
    poin[1,:] = dx[1:]
    
    n_bins = int(2 / l_bin)
    hist, edges = np.histogramdd(poin.T, bins = n_bins, range=[[-1,1],[-1,1]])
    total_bins = np.count_nonzero(hist)
    i_bin_central_2 = int(n_bins / 2)
    i_bin_central_1 = i_bin_central_2 - 1
    bins_centrales = list(hist[i_bin_central_1][i_bin_central_1:i_bin_central_2+1]) + list(hist[i_bin_central_2][i_bin_central_1:i_bin_central_2+1])
    total_bins_centrales = np.count_nonzero(bins_centrales)
    corrected_bins = int(total_bins - total_bins_centrales)

    if correct == False:
        corrected_bins = total_bins

    return corrected_bins


def dif_50ms(serie, tol = 0.05):
    contador = 0
    if len(serie) > 0:
        for i in range(0, len(serie) - 1):
            x = serie[i]
            x_next = serie[i+1]
            if (x_next - tol < x < x_next + tol) and (abs(x_next - x) < 3*tol):
                contador = contador + 1
    return contador / len(serie)

