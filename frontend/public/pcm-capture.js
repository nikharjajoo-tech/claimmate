// AudioWorklet that forwards raw microphone samples to the page (see src/lib/audio.ts).
// Served as a static file so the Content-Security-Policy can stay script-src 'self'.
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
