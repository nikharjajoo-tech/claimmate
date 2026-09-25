// Camera streaming and photo upload helpers. Frames go to the server as base64 JPEG.

export const FRAME_INTERVAL_MS = 1000;
const FRAME_MAX_WIDTH = 640;
const UPLOAD_MAX_SIDE = 1280;

export function stripDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(",");
  return comma === -1 ? dataUrl : dataUrl.slice(comma + 1);
}

/** Largest size that fits within maxWidth/maxSide while keeping the aspect ratio. */
export function fitWithin(width: number, height: number, maxSide: number): { width: number; height: number } {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

/** Streams the camera into a <video> preview and emits a JPEG frame about once a second. */
export class CameraStream {
  private stream: MediaStream | null = null;
  private timer: number | null = null;
  private canvas = document.createElement("canvas");

  async start(video: HTMLVideoElement, onFrame: (jpegBase64: string) => void, onEnded: () => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: FRAME_MAX_WIDTH } },
    });
    this.stream.getVideoTracks()[0]?.addEventListener("ended", onEnded); // e.g. device unplugged
    video.srcObject = this.stream;
    await video.play().catch(() => undefined);
    this.timer = window.setInterval(() => {
      if (!video.videoWidth) return; // not ready yet
      const size = fitWithin(video.videoWidth, video.videoHeight, FRAME_MAX_WIDTH);
      this.canvas.width = size.width;
      this.canvas.height = size.height;
      this.canvas.getContext("2d")?.drawImage(video, 0, 0, size.width, size.height);
      onFrame(stripDataUrl(this.canvas.toDataURL("image/jpeg", 0.7)));
    }, FRAME_INTERVAL_MS);
  }

  stop(video?: HTMLVideoElement | null): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (video) video.srcObject = null;
  }
}

/** Converts any browser-readable image (JPEG, PNG, HEIC where supported) to a bounded JPEG. */
export async function fileToJpegBase64(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const size = fitWithin(bitmap.width, bitmap.height, UPLOAD_MAX_SIDE);
  const canvas = document.createElement("canvas");
  canvas.width = size.width;
  canvas.height = size.height;
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, size.width, size.height);
  bitmap.close();
  return stripDataUrl(canvas.toDataURL("image/jpeg", 0.8));
}
