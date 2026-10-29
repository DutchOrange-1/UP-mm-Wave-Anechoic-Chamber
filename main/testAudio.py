import winsound

# Play a local WAV file
winsound.PlaySound("./supplementary/finish_audio.wav", winsound.SND_FILENAME)

# Or simply make a beep sound (Frequency in Hz, Duration in milliseconds)
# winsound.Beep(1000, 500)
