/**
 * AudioWorklet processor — downsamples browser audio to 16kHz int16 PCM
 * and sends 100ms chunks to the main thread.
 */
class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._ratio = sampleRate / 16000; // e.g. 48000/16000 = 3
        this._buf = [];
        this._chunk = 1600; // 100ms at 16kHz
    }

    process(inputs) {
        const ch = inputs[0]?.[0];
        if (!ch) return true;

        // Linear-interpolation downsample
        for (let i = 0; i < ch.length; i += this._ratio) {
            const lo = ch[Math.floor(i)] ?? 0;
            const hi = ch[Math.min(Math.ceil(i), ch.length - 1)] ?? lo;
            this._buf.push(lo + (hi - lo) * (i % 1));
        }

        while (this._buf.length >= this._chunk) {
            const samples = this._buf.splice(0, this._chunk);
            const pcm = new Int16Array(samples.length);
            for (let i = 0; i < samples.length; i++) {
                pcm[i] = Math.max(-32768, Math.min(32767, samples[i] * 32767));
            }
            this.port.postMessage({ pcm: pcm.buffer }, [pcm.buffer]);
        }
        return true;
    }
}

registerProcessor('pcm-processor', PCMProcessor);
