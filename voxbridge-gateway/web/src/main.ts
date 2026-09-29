import { App, type McpUiToolResultNotification } from "@modelcontextprotocol/ext-apps";
import "./styles.css";

type ToolResult = McpUiToolResultNotification["params"];
type ResultContent = NonNullable<ToolResult["content"]>[number];
type ResourceLink = Extract<ResultContent, { type: "resource_link" }>;
type AudioContent = Extract<ResultContent, { type: "audio" }>;
type DeliveryMode = "playback" | "file" | "both";

type SpeechMetadata = {
  delivery?: DeliveryMode;
  download_expires_at?: string;
  file_mime_type?: string;
  file_name?: string;
  file_size_bytes?: number;
  mime_type?: string;
  playback_included?: boolean;
  provider?: string;
};

const app = new App(
  { name: "VoxBridge Audio Delivery", version: "0.2.0" },
  { availableDisplayModes: ["inline"] },
  { autoResize: true },
);

const summary = requiredElement<HTMLParagraphElement>("summary");
const details = requiredElement<HTMLDListElement>("details");
const mode = requiredElement<HTMLElement>("mode");
const fileName = requiredElement<HTMLElement>("file-name");
const format = requiredElement<HTMLElement>("format");
const fileSize = requiredElement<HTMLElement>("file-size");
const expiryRow = requiredElement<HTMLElement>("expiry-row");
const expiry = requiredElement<HTMLElement>("expiry");
const playback = requiredElement<HTMLElement>("playback");
const playPauseButton = requiredElement<HTMLButtonElement>("play-pause");
const playbackIcon = requiredElement<HTMLElement>("playback-icon");
const playbackLabel = requiredElement<HTMLElement>("playback-label");
const actions = requiredElement<HTMLElement>("actions");
const downloadButton = requiredElement<HTMLButtonElement>("download");
const buttonLabel = downloadButton.querySelector<HTMLElement>(".button-label");
const status = requiredElement<HTMLParagraphElement>("status");

if (!buttonLabel) {
  throw new Error("Missing download button label");
}

const resolvedButtonLabel = buttonLabel;

let currentResource: ResourceLink | undefined;
let hostCanDownload = false;
let isDownloading = false;
let latestResult: ToolResult | undefined;
let audioPlayer: HTMLAudioElement | undefined;
let audioObjectUrl: string | undefined;

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: ${id}`);
  }
  return element as T;
}

function setStatus(message: string, kind: "neutral" | "success" | "error" = "neutral") {
  status.textContent = message;
  status.dataset.kind = kind;
  status.setAttribute("role", kind === "error" ? "alert" : "status");
}

function setDownloading(value: boolean) {
  isDownloading = value;
  downloadButton.disabled = value || !hostCanDownload;
  downloadButton.setAttribute("aria-busy", String(value));
  resolvedButtonLabel.textContent = value ? "Preparing download…" : "Download audio";
}

function parseMetadata(content: ToolResult["content"]): SpeechMetadata {
  for (const item of content ?? []) {
    if (item.type !== "text") continue;
    try {
      const value: unknown = JSON.parse(item.text);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as SpeechMetadata;
      }
    } catch {
      // A text block may be a human-readable message rather than metadata.
    }
  }
  return {};
}

function findResourceLink(content: ToolResult["content"]): ResourceLink | undefined {
  return (content ?? []).find(
    (item): item is ResourceLink =>
      item.type === "resource_link" && item.uri.startsWith("voxbridge://audio/"),
  );
}

function findAudioContent(content: ToolResult["content"]): AudioContent | undefined {
  return (content ?? []).find((item): item is AudioContent => item.type === "audio");
}

function clearPlayback() {
  if (audioPlayer) {
    audioPlayer.pause();
    audioPlayer.removeAttribute("src");
    audioPlayer.load();
  }
  if (audioObjectUrl) {
    URL.revokeObjectURL(audioObjectUrl);
  }
  audioPlayer = undefined;
  audioObjectUrl = undefined;
  playback.hidden = true;
  playPauseButton.disabled = false;
  playPauseButton.setAttribute("aria-pressed", "false");
  playbackIcon.textContent = "▶";
  playbackLabel.textContent = "Play audio";
}

function preparePlayback(audio: AudioContent | undefined) {
  clearPlayback();
  if (!audio) return false;

  try {
    if (!audio.mimeType.startsWith("audio/") || !/^[A-Za-z0-9+/]*={0,2}$/.test(audio.data)) {
      throw new Error("Invalid audio content");
    }
    const decoded = atob(audio.data);
    if (decoded.length === 0) {
      throw new Error("Empty audio content");
    }
    const bytes = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) {
      bytes[index] = decoded.charCodeAt(index);
    }
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: audio.mimeType }));
    const player = new Audio(objectUrl);
    audioObjectUrl = objectUrl;
    audioPlayer = player;
    player.preload = "metadata";
    player.addEventListener("play", () => {
      if (audioPlayer !== player) return;
      playPauseButton.setAttribute("aria-pressed", "true");
      playbackIcon.textContent = "❚❚";
      playbackLabel.textContent = "Pause audio";
      setStatus("Playing audio.");
    });
    player.addEventListener("pause", () => {
      if (audioPlayer !== player || player.ended) return;
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play audio";
    });
    player.addEventListener("ended", () => {
      if (audioPlayer !== player) return;
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play again";
      setStatus("Playback complete.", "success");
    });
    player.addEventListener("error", () => {
      if (audioPlayer !== player) return;
      playPauseButton.disabled = true;
      setStatus("This host could not play the generated audio.", "error");
    });
    playback.hidden = false;
    return true;
  } catch {
    clearPlayback();
    return false;
  }
}

function deriveMode(metadata: SpeechMetadata, content: ToolResult["content"]): DeliveryMode {
  if (metadata.delivery === "playback" || metadata.delivery === "file" || metadata.delivery === "both") {
    return metadata.delivery;
  }
  const hasAudio = (content ?? []).some((item) => item.type === "audio");
  const hasFile = Boolean(findResourceLink(content));
  return hasAudio && hasFile ? "both" : hasFile ? "file" : "playback";
}

function modeLabel(value: DeliveryMode) {
  if (value === "both") return "Playback + file";
  return value === "file" ? "File only" : "Playback only";
}

function formatBytes(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let amount = value / 1024;
  let unit = units[0];
  for (let index = 1; amount >= 1024 && index < units.length; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

function formatExpiry(value: string | undefined) {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function renderResult(result: ToolResult) {
  latestResult = result;
  if (result.isError) {
    clearPlayback();
    currentResource = undefined;
    actions.hidden = true;
    details.hidden = true;
    summary.textContent = "Audio generation did not complete.";
    setStatus("The speech tool returned an error.", "error");
    return;
  }

  const metadata = parseMetadata(result.content);
  const resource = findResourceLink(result.content);
  const audio = findAudioContent(result.content);
  const delivery = deriveMode(metadata, result.content);
  const expiresAt = formatExpiry(metadata.download_expires_at);

  currentResource = resource;
  const playbackReady = preparePlayback(audio);
  details.hidden = false;
  mode.textContent = modeLabel(delivery);
  fileName.textContent = resource?.name ?? metadata.file_name ?? "No file included";
  format.textContent = resource?.mimeType ?? metadata.file_mime_type ?? metadata.mime_type ?? "—";
  fileSize.textContent = formatBytes(resource?.size ?? metadata.file_size_bytes);
  expiryRow.hidden = !expiresAt;
  expiry.textContent = expiresAt ?? "—";

  if (delivery === "both") {
    summary.textContent = "Your audio is ready to play and download.";
  } else if (delivery === "file") {
    summary.textContent = "Your downloadable audio file is ready.";
  } else {
    summary.textContent = "Your audio is ready for playback.";
  }

  actions.hidden = !resource || !hostCanDownload;
  setDownloading(false);
  if (!resource && playbackReady) {
    setStatus("Ready to play. Playback mode does not include a downloadable file.");
  } else if (!resource) {
    setStatus("The generated audio could not be prepared for playback.", "error");
  } else if (audio && !playbackReady) {
    setStatus("The file is ready, but playback could not be prepared.", "error");
  } else if (!hostCanDownload) {
    setStatus("Use the host's attached-file Download action below.");
  } else {
    setStatus("Ready to download.");
  }
}

async function togglePlayback() {
  if (!audioPlayer) return;
  if (!audioPlayer.paused) {
    audioPlayer.pause();
    setStatus("Playback paused.");
    return;
  }
  try {
    await audioPlayer.play();
  } catch {
    setStatus("Playback could not start. Try the play button again.", "error");
  }
}

async function downloadCurrentResource() {
  if (isDownloading || !currentResource) return;
  if (!hostCanDownload) {
    setStatus("This host does not support native file downloads.", "error");
    return;
  }

  setDownloading(true);
  setStatus("Waiting for download confirmation…");
  try {
    const result = await app.downloadFile({ contents: [currentResource] });
    if (result.isError) {
      setStatus("Download cancelled or declined by the host.");
      return;
    }
    setStatus("Download started successfully.", "success");
  } catch {
    setStatus("Download failed. The temporary audio file may have expired.", "error");
  } finally {
    setDownloading(false);
  }
}

downloadButton.addEventListener("click", () => {
  void downloadCurrentResource();
});

playPauseButton.addEventListener("click", () => {
  void togglePlayback();
});

app.addEventListener("toolinput", (params) => {
  const requested = params.arguments?.delivery;
  if (requested === "playback" || requested === "file" || requested === "both") {
    mode.textContent = modeLabel(requested);
  }
  latestResult = undefined;
  currentResource = undefined;
  clearPlayback();
  actions.hidden = true;
  summary.textContent = "Generating audio…";
  setStatus("Waiting for the speech provider.");
});

app.addEventListener("toolresult", renderResult);

app.addEventListener("toolcancelled", (params) => {
  currentResource = undefined;
  clearPlayback();
  actions.hidden = true;
  summary.textContent = "Audio generation was cancelled.";
  setStatus(params.reason ? `Cancelled: ${params.reason}` : "Cancelled.");
});

app.onteardown = () => {
  clearPlayback();
  return {};
};

window.addEventListener("pagehide", clearPlayback);

try {
  await app.connect();
  hostCanDownload = app.getHostCapabilities()?.downloadFile !== undefined;
  downloadButton.disabled = !hostCanDownload;
  if (latestResult) {
    renderResult(latestResult);
  } else {
    setStatus(
      hostCanDownload
        ? "Connected. Waiting for generated audio…"
        : "Connected. File results will use the host's attached-file Download action.",
    );
  }
} catch (error) {
  const message = error instanceof Error ? error.message : "Unknown error";
  setStatus(`Could not connect to the host: ${message}`, "error");
}
