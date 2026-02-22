import sounddevice as sd
for i, d in enumerate(sd.query_devices()):
    ch_in = d['max_input_channels']
    ch_out = d['max_output_channels']
    sr = int(d['default_samplerate'])
    io = 'I' if ch_in > 0 else ' '
    io += 'O' if ch_out > 0 else ' '
    print(f"  {i:2d} [{io}] {d['name']:<45s} {sr}Hz  in={ch_in} out={ch_out}")
