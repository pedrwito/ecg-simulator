import neurokit2 as nk
import matplotlib.pyplot as plt
import numpy as np

# Simulation parameters
duration = 30  # in seconds
sampling_rate = 1000  # in Hz
heart_rate = 150  # beats per minute
respiratory_rate = 15  # breaths per minute

# Simulate ECG signal
ecg = nk.ecg_simulate(duration=duration, sampling_rate=sampling_rate, heart_rate=heart_rate, method = "multileads")
print(np.array(ecg["I"]))

# Simulate PPG signal
ppg = nk.ppg_simulate(duration=duration, sampling_rate=sampling_rate, heart_rate=heart_rate)

# Simulate respiratory signal
respiratory = nk.rsp_simulate(duration=duration, sampling_rate=sampling_rate, respiratory_rate=respiratory_rate)

# Create time vector
time = np.linspace(0, duration, len(ecg))

# Plotting all signals together
plt.figure(figsize=(12, 9))

# Plot ECG
plt.subplot(3, 1, 1)
plt.plot(time, ecg, label='ECG Signal')
plt.title('ECG Signal')
plt.xlabel('Time (s)')
plt.ylabel('Amplitude')
plt.grid(True)

# Plot PPG
plt.subplot(3, 1, 2)
plt.plot(time, ppg, label='PPG Signal', color='red')
plt.title('PPG Signal')
plt.xlabel('Time (s)')
plt.ylabel('Amplitude')
plt.grid(True)

# Plot Respiratory
plt.subplot(3, 1, 3)
plt.plot(time, respiratory, label='Respiratory Signal', color='green')
plt.title('Respiratory Signal')
plt.xlabel('Time (s)')
plt.ylabel('Amplitude')
plt.grid(True)

plt.tight_layout()
plt.show()