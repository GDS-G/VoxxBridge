import { App, type McpUiToolResultNotification } from "@modelcontextprotocol/ext-apps";
import "./styles.css";

type ToolResult = McpUiToolResultNotification["params"];
type ResultContent = NonNullable<ToolResult["content"]>[number];
type ResourceLink = Extract<ResultContent, { type: "resource_link" }>;
type AudioContent = Extract<ResultContent, { type: "audio" }>;
type ResourceReadResult = Awaited<ReturnType<App["readServerResource"]>>;
type ResourceReadContent = ResourceReadResult["contents"][number];
type BlobResourceContents = Extract<ResourceReadContent, { blob: string }>;
type ModelContextUpdate = Parameters<App["updateModelContext"]>[0];
type ModelContextContent = NonNullable<ModelContextUpdate["content"]>[number];
type EmbeddedResource = Extract<ModelContextContent, { type: "resource" }>;
type DeliveryMode = "playback" | "file" | "both";
type AppAudioMetadata = Pick<AudioContent, "data" | "mimeType">;
type PlaybackState =
  | "unavailable"
  | "resource"
  | "ready"
  | "decoding"
  | "playing"
  | "paused"
  | "ended";

type AudioMetadata = {
  app_resource_playback?: boolean;
  delivery?: DeliveryMode;
  download_expires_at?: string;
  file_mime_type?: string;
  file_name?: string;
  file_size_bytes?: number;
  inline_audio_included?: boolean;
  materialize_max_bytes?: number;
  mime_type?: string;
  playback_requested?: boolean;
  provider?: string;
  sha256?: string;
};

type OpenAIContext = {
  toolResponseMetadata?: unknown;
};

// VoxBridge keeps the optional host handoff bounded independently of any
// host-specific MIME or size policy.
const DEFAULT_MAX_VOXBRIDGE_CHATGPT_HANDOFF_BYTES = 8 * 1024 * 1024;
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const app = new App(
  { name: "VoxBridge Audio Delivery", version: "0.4.0" },
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
const saveChatGptButton = requiredElement<HTMLButtonElement>("save-chatgpt");
const saveChatGptLabel = saveChatGptButton.querySelector<HTMLElement>(".button-label");
const status = requiredElement<HTMLParagraphElement>("status");

if (!buttonLabel || !saveChatGptLabel) {
  throw new Error("Missing file action button label");
}

const resolvedButtonLabel = buttonLabel;
const resolvedSaveChatGptLabel = saveChatGptLabel;

let currentResource: ResourceLink | undefined;
let isConnected = false;
let hostCanDownload = false;
let hostCanReadResources = false;
let hostCanCallTools = false;
let hostCanAttachToChatGpt = false;
let isDownloading = false;
let isMaterializingForChatGpt = false;
let materializedForChatGpt = false;
let modelContextResourceUri: string | undefined;
let cachedFileResourceUri: string | undefined;
let cachedFileBase64: string | undefined;
let cachedFileBytes: ArrayBuffer | undefined;
let cachedFileMimeType: string | undefined;
let currentMetadata: AudioMetadata = {};
let latestResult: ToolResult | undefined;
let resultRevision = 0;
let modelContextUpdateSequence = 0;
let modelContextUpdateQueue: Promise<void> = Promise.resolve();
let playbackRevision = 0;
let playbackResource: ResourceLink | undefined;
let playbackBytes: ArrayBuffer | undefined;
let playbackContext: AudioContext | undefined;
let playbackBuffer: AudioBuffer | undefined;
let playbackSource: AudioBufferSourceNode | undefined;
let playbackState: PlaybackState = "unavailable";
let playbackOffsetSeconds = 0;
let playbackStartedAt = 0;

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: ${id}`);
  }
  return element as T;
}

function getOpenAIContext(): OpenAIContext | undefined {
  return (window as typeof window & { openai?: OpenAIContext }).openai;
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

function refreshChatGptButton() {
  saveChatGptButton.disabled =
    isMaterializingForChatGpt || materializedForChatGpt || !currentResource;
  saveChatGptButton.setAttribute("aria-busy", String(isMaterializingForChatGpt));
  resolvedSaveChatGptLabel.textContent = isMaterializingForChatGpt
    ? "Adding file to ChatGPT…"
    : materializedForChatGpt
      ? "Added to ChatGPT"
      : "Add file to ChatGPT";
}

function setMaterializingForChatGpt(value: boolean) {
  isMaterializingForChatGpt = value;
  refreshChatGptButton();
}

function resetFileHandoff() {
  isMaterializingForChatGpt = false;
  materializedForChatGpt = false;
  modelContextResourceUri = undefined;
  cachedFileResourceUri = undefined;
  cachedFileBase64 = undefined;
  cachedFileBytes = undefined;
  cachedFileMimeType = undefined;
  refreshChatGptButton();
}

function scheduleModelContextUpdate(content: ModelContextContent[], sequence: number) {
  const operation = modelContextUpdateQueue.then(async () => {
    if (sequence !== modelContextUpdateSequence) return;
    await app.updateModelContext({ content });
  });
  modelContextUpdateQueue = operation.catch(() => undefined);
  return operation;
}

function invalidateChatGptAttachment(clearAttached: boolean) {
  if (!isMaterializingForChatGpt && !materializedForChatGpt) return;
  const shouldClear =
    hostCanAttachToChatGpt &&
    (isMaterializingForChatGpt || (clearAttached && materializedForChatGpt));
  const sequence = ++modelContextUpdateSequence;
  if (shouldClear) {
    void scheduleModelContextUpdate([], sequence);
  }
}

function syncChatGptAttachmentFromHostContext(value: unknown, announceRemoval: boolean) {
  const hostContext = asRecord(value);
  if (
    !hostContext ||
    !Object.prototype.hasOwnProperty.call(hostContext, "openai/modelContext")
  ) {
    return;
  }

  const state = hostContext["openai/modelContext"];
  let attached: boolean | undefined;
  if (state === null) {
    attached = false;
  } else {
    const content = asRecord(state)?.content;
    if (Array.isArray(content) && currentResource) {
      const expectedUri =
        modelContextResourceUri ?? `file:///${encodeURIComponent(currentResource.name)}`;
      attached = content.some((candidate) => {
        const block = asRecord(candidate);
        const resource = asRecord(block?.resource);
        return block?.type === "resource" && resource?.uri === expectedUri;
      });
    }
  }
  if (attached === undefined || attached === materializedForChatGpt) return;

  const wasAttached = materializedForChatGpt;
  materializedForChatGpt = attached;
  updateFileActions(currentResource);
  if (attached) {
    setStatus(
      "The exact audio file is attached to ChatGPT's composer. Send your next message to share it with the model.",
      "success",
    );
  } else if (wasAttached && announceRemoval && currentResource) {
    setStatus(
      "The composer no longer contains this audio file. Choose Add file to ChatGPT to attach it again without regenerating audio.",
    );
  }
}

function canChatGptHandoff(
  resource: ResourceLink | undefined,
  metadata: AudioMetadata = currentMetadata,
) {
  const handoffSize = resource?.size ?? metadata.file_size_bytes;
  const configuredLimit = metadata.materialize_max_bytes;
  const handoffLimit =
    typeof configuredLimit === "number" &&
    Number.isFinite(configuredLimit) &&
    configuredLimit >= 0
      ? configuredLimit
      : DEFAULT_MAX_VOXBRIDGE_CHATGPT_HANDOFF_BYTES;
  return Boolean(
    resource &&
    hostCanCallTools &&
    hostCanAttachToChatGpt &&
    (handoffSize === undefined || handoffSize <= handoffLimit),
  );
}

function updateFileActions(resource: ResourceLink | undefined) {
  const canSaveToChatGpt = canChatGptHandoff(resource);
  refreshChatGptButton();
  downloadButton.hidden = !hostCanDownload;
  saveChatGptButton.hidden = !canSaveToChatGpt;
  actions.hidden = !resource || (!hostCanDownload && !canSaveToChatGpt);
}

function parseMetadata(content: ToolResult["content"]): AudioMetadata {
  for (const item of content ?? []) {
    if (item.type !== "text") continue;
    try {
      const value: unknown = JSON.parse(item.text);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as AudioMetadata;
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

function isBlobResourceContents(content: ResourceReadContent): content is BlobResourceContents {
  return "blob" in content && typeof content.blob === "string";
}

function decodeBase64Audio(data: string, mimeType: string): ArrayBuffer {
  if (
    !mimeType.toLowerCase().startsWith("audio/") ||
    data.length === 0 ||
    !BASE64_PATTERN.test(data)
  ) {
    throw new Error("Invalid base64 audio content");
  }

  const decoded = atob(data);
  if (decoded.length === 0) {
    throw new Error("Empty audio content");
  }

  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes.buffer;
}

function base64DecodedByteLength(data: string) {
  if (data.length === 0 || !BASE64_PATTERN.test(data)) {
    throw new Error("Invalid base64 audio content");
  }
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return (data.length / 4) * 3 - padding;
}

async function verifyFileBytes(
  bytes: ArrayBuffer,
  resource: ResourceLink,
  metadata: AudioMetadata,
) {
  const expectedSize = resource.size ?? metadata.file_size_bytes;
  if (expectedSize !== undefined && bytes.byteLength !== expectedSize) {
    throw new Error("Generated audio size does not match its file metadata");
  }
  const expectedDigest = metadata.sha256?.toLowerCase();
  if (!expectedDigest) return;
  if (!/^[a-f0-9]{64}$/.test(expectedDigest)) {
    throw new Error("Generated audio has invalid integrity metadata");
  }
  if (!globalThis.crypto?.subtle) {
    throw new Error("This host cannot verify the generated audio integrity metadata");
  }
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes.slice(0));
  const actualDigest = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (actualDigest !== expectedDigest) {
    throw new Error("Generated audio failed its integrity check");
  }
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function envelopeContainsResourceUri(value: unknown, resourceUri: string) {
  const pending: unknown[] = [value];
  const seen = new Set<object>();
  for (let index = 0; index < pending.length && index < 40; index += 1) {
    const candidate = pending[index];
    if (Array.isArray(candidate)) {
      pending.push(...candidate.slice(0, 20));
      continue;
    }
    const record = asRecord(candidate);
    if (!record || seen.has(record)) continue;
    seen.add(record);
    if (record.type === "resource_link" && record.uri === resourceUri) return true;
    pending.push(
      record.content,
      record.call_tool_result,
      record.mcp_tool_result,
      record.callToolResult,
      record.mcpToolResult,
      record.result,
    );
  }
  return false;
}

function parseAppAudioValue(value: unknown): AppAudioMetadata | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;

  const data = "data" in value ? value.data : undefined;
  const mimeType = "mimeType" in value ? value.mimeType : undefined;
  if (typeof data !== "string" || typeof mimeType !== "string") return undefined;
  return { data, mimeType };
}

function parseAppAudioMetadata(
  result: ToolResult,
  resourceUri: string,
): AppAudioMetadata | undefined {
  // ChatGPT's widget-only response metadata can preserve the complete MCP result
  // envelope, including hidden _meta. Check the known envelope branches only
  // when they also identify the current resource URI.
  const pending: unknown[] = [result];
  const responseMetadata = getOpenAIContext()?.toolResponseMetadata;
  if (envelopeContainsResourceUri(responseMetadata, resourceUri)) {
    pending.push(responseMetadata);
  }
  const seen = new Set<object>();
  for (let index = 0; index < pending.length && index < 12; index += 1) {
    const record = asRecord(pending[index]);
    if (!record || seen.has(record)) continue;
    seen.add(record);

    const direct = parseAppAudioValue(record["voxbridge/audio"]);
    if (direct) return direct;
    const hidden = parseAppAudioValue(asRecord(record._meta)?.["voxbridge/audio"]);
    if (hidden) return hidden;

    pending.push(
      record.call_tool_result,
      record.mcp_tool_result,
      record.callToolResult,
      record.mcpToolResult,
      record.result,
    );
  }
  return undefined;
}

function parseResourceAudio(result: ResourceReadResult, resource: ResourceLink) {
  for (const content of result.contents) {
    if (!isBlobResourceContents(content)) continue;
    const mimeType = content.mimeType ?? resource.mimeType;
    if (mimeType?.toLowerCase().startsWith("audio/")) {
      return { data: content.blob, mimeType };
    }
  }
  throw new Error("Expected a base64 audio blob resource");
}

async function loadFileResource(resource: ResourceLink) {
  if (
    cachedFileResourceUri === resource.uri &&
    cachedFileBase64 &&
    cachedFileMimeType
  ) {
    return { data: cachedFileBase64, mimeType: cachedFileMimeType };
  }
  if (!hostCanReadResources) {
    throw new Error("This host cannot read the generated audio resource");
  }
  const result = await app.readServerResource({ uri: resource.uri });
  const audio = parseResourceAudio(result, resource);
  cachedFileResourceUri = resource.uri;
  cachedFileBase64 = audio.data;
  cachedFileMimeType = audio.mimeType;
  return audio;
}

function verifyFileEnvelope(
  data: string,
  resource: ResourceLink,
  metadata: AudioMetadata,
) {
  const expectedSize = resource.size ?? metadata.file_size_bytes;
  if (expectedSize !== undefined && base64DecodedByteLength(data) !== expectedSize) {
    throw new Error("Generated audio size does not match its file metadata");
  }
}

function stopPlaybackSource() {
  const source = playbackSource;
  playbackSource = undefined;
  if (!source) return;

  source.onended = null;
  try {
    source.stop();
  } catch {
    // The source may already have ended.
  }
  try {
    source.disconnect();
  } catch {
    // The source may already have been disconnected.
  }
}

function closePlaybackContext(context: AudioContext | undefined) {
  if (!context || context.state === "closed") return;
  void context.close().catch(() => {
    // Closing is best-effort during replacement or teardown.
  });
}

function clearPlayback() {
  playbackRevision += 1;
  stopPlaybackSource();
  const context = playbackContext;
  playbackContext = undefined;
  playbackResource = undefined;
  playbackBytes = undefined;
  playbackBuffer = undefined;
  playbackState = "unavailable";
  playbackOffsetSeconds = 0;
  playbackStartedAt = 0;
  closePlaybackContext(context);
  playback.hidden = true;
  playPauseButton.disabled = false;
  playPauseButton.setAttribute("aria-busy", "false");
  playPauseButton.setAttribute("aria-pressed", "false");
  playbackIcon.textContent = "▶";
  playbackLabel.textContent = "Play audio";
}

function prepareResourcePlayback(resource: ResourceLink) {
  clearPlayback();
  playbackResource = resource;
  playbackState = "resource";
  playback.hidden = false;
}

function preparePlayback(audio: Pick<AudioContent, "data" | "mimeType"> | undefined) {
  clearPlayback();
  if (!audio) return false;

  try {
    playbackBytes = decodeBase64Audio(audio.data, audio.mimeType);
    playbackState = "ready";
    playback.hidden = false;
    return true;
  } catch {
    playbackBytes = undefined;
    return false;
  }
}

function deriveMode(metadata: AudioMetadata, content: ToolResult["content"]): DeliveryMode {
  if (metadata.delivery === "playback" || metadata.delivery === "file" || metadata.delivery === "both") {
    return metadata.delivery;
  }
  const hasAudio = (content ?? []).some((item) => item.type === "audio");
  const hasFile = Boolean(findResourceLink(content));
  if (hasFile && metadata.app_resource_playback === true) return "both";
  if (hasFile && metadata.playback_requested === false) return "file";
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

function fileDeliveryStatus() {
  return hostCanDownload
    ? "The audio file remains ready to download."
    : canChatGptHandoff(currentResource)
      ? "Choose Add file to ChatGPT below to attach the exact existing VoxBridge file without regenerating it."
      : "Ask ChatGPT to retrieve the existing VoxBridge file; do not regenerate it.";
}

function showPlaybackUnavailable(reason: string) {
  clearPlayback();
  summary.textContent = "Your downloadable audio file is ready, but playback is unavailable.";
  setStatus(`${reason} ${fileDeliveryStatus()}`, "error");
}

function renderResult(result: ToolResult) {
  latestResult = result;
  resultRevision += 1;
  clearPlayback();
  resetFileHandoff();
  if (result.isError) {
    currentResource = undefined;
    currentMetadata = {};
    actions.hidden = true;
    details.hidden = true;
    summary.textContent = "Audio generation did not complete.";
    setStatus("The audio generation tool returned an error.", "error");
    return;
  }

  const metadata = parseMetadata(result.content);
  const resource = findResourceLink(result.content);
  const audio = findAudioContent(result.content);
  const delivery = deriveMode(metadata, result.content);
  const expiresAt = formatExpiry(metadata.download_expires_at);

  currentResource = resource;
  currentMetadata = metadata;
  details.hidden = false;
  mode.textContent = modeLabel(delivery);
  fileName.textContent = resource?.name ?? metadata.file_name ?? "No file included";
  format.textContent = resource?.mimeType ?? metadata.file_mime_type ?? metadata.mime_type ?? "—";
  fileSize.textContent = formatBytes(resource?.size ?? metadata.file_size_bytes);
  expiryRow.hidden = !expiresAt;
  expiry.textContent = expiresAt ?? "—";

  if (delivery === "both") {
    summary.textContent = hostCanDownload
      ? "Your audio is ready to play and download."
      : "Your audio is ready to play, and its file resource is available.";
  } else if (delivery === "file") {
    summary.textContent = hostCanDownload
      ? "Your downloadable audio file is ready."
      : "Your audio file resource is ready.";
  } else {
    summary.textContent = "Your audio is ready for playback.";
  }

  updateFileActions(resource);
  setDownloading(false);

  if (delivery === "playback") {
    if (preparePlayback(audio)) {
      setStatus("Ready to play. Playback mode does not include a downloadable file.");
    } else {
      setStatus("The inline audio could not be prepared for playback.", "error");
    }
    return;
  }

  if (delivery === "both") {
    if (!resource) {
      summary.textContent = "Audio generation did not include a downloadable file.";
      setStatus("Playback and file delivery could not be prepared.", "error");
      return;
    }
    const appAudio = parseAppAudioMetadata(result, resource.uri);
    if (appAudio) {
      cachedFileResourceUri = resource.uri;
      cachedFileBase64 = appAudio.data;
      cachedFileMimeType = appAudio.mimeType;
    }
    if (appAudio && preparePlayback(appAudio)) {
      cachedFileBytes = playbackBytes;
      setStatus(
        hostCanDownload
          ? "Ready to play or download."
          : `Ready to play. ${fileDeliveryStatus()}`,
      );
      return;
    }
    if (!isConnected) {
      setStatus(
        "Waiting for the host connection before preparing playback. The attached audio file remains available below.",
      );
      return;
    }
    if (!hostCanReadResources) {
      showPlaybackUnavailable(
        "Playback is unavailable because this host does not support server resource reads.",
      );
      return;
    }
    prepareResourcePlayback(resource);
    setStatus(
      hostCanDownload
        ? "Ready to download. Choose Play audio to load the file for playback."
        : `Choose Play audio to load the file for playback. ${fileDeliveryStatus()}`,
    );
    return;
  }

  if (!resource) {
    setStatus("The generated audio did not include a downloadable file.", "error");
  } else if (!hostCanDownload) {
    setStatus(fileDeliveryStatus());
  } else {
    setStatus("Ready to download.");
  }
}

function pausePlayback() {
  if (
    playbackState !== "playing" ||
    !playbackContext ||
    !playbackBuffer ||
    !playbackSource
  ) {
    return;
  }

  const elapsed = Math.max(0, playbackContext.currentTime - playbackStartedAt);
  playbackOffsetSeconds = Math.min(
    playbackBuffer.duration,
    playbackOffsetSeconds + elapsed,
  );
  stopPlaybackSource();
  playbackState = playbackOffsetSeconds >= playbackBuffer.duration ? "ended" : "paused";
  playPauseButton.setAttribute("aria-pressed", "false");
  playbackIcon.textContent = "▶";
  playbackLabel.textContent = playbackState === "ended" ? "Play again" : "Play audio";
  if (playbackState === "ended") {
    setStatus("Playback complete.", "success");
  } else {
    setStatus("Playback paused.");
  }
}

async function togglePlayback() {
  if (playbackState === "playing") {
    pausePlayback();
    return;
  }

  if (playbackState === "decoding" || (!playbackBytes && !playbackResource)) return;

  const revision = playbackRevision;
  const resource = playbackResource;
  let bytes = playbackBytes;
  const previousState = playbackState;
  if (previousState === "ended") {
    playbackOffsetSeconds = 0;
  }
  playbackState = "decoding";
  playPauseButton.disabled = true;
  playPauseButton.setAttribute("aria-busy", "true");
  playbackLabel.textContent = bytes ? "Preparing audio…" : "Loading audio…";
  setStatus(bytes ? "Preparing playback…" : "Loading audio for playback…");

  let context = playbackContext;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext();
      playbackContext = context;
    }

    const resumePromise = context.state === "suspended" ? context.resume() : Promise.resolve();
    if (!bytes) {
      if (!resource) return;
      const [result] = await Promise.all([
        app.readServerResource({ uri: resource.uri }),
        resumePromise,
      ]);
      if (
        revision !== playbackRevision ||
        playbackResource?.uri !== resource.uri ||
        playbackContext !== context
      ) {
        return;
      }
      const resourceAudio = parseResourceAudio(result, resource);
      bytes = decodeBase64Audio(resourceAudio.data, resourceAudio.mimeType);
      playbackBytes = bytes;
    } else {
      await resumePromise;
    }

    const decodePromise: Promise<AudioBuffer> = playbackBuffer
      ? Promise.resolve(playbackBuffer)
      : context.decodeAudioData(bytes.slice(0));
    const decodedBuffer = await decodePromise;

    if (
      revision !== playbackRevision ||
      playbackBytes !== bytes ||
      playbackContext !== context
    ) {
      return;
    }
    if (!Number.isFinite(decodedBuffer.duration) || decodedBuffer.duration <= 0) {
      throw new Error("Decoded audio is empty");
    }

    playbackBuffer = decodedBuffer;
    if (playbackOffsetSeconds >= decodedBuffer.duration) {
      playbackOffsetSeconds = 0;
    }

    const source = context.createBufferSource();
    source.buffer = decodedBuffer;
    source.connect(context.destination);
    playbackSource = source;
    source.onended = () => {
      if (revision !== playbackRevision || playbackSource !== source) return;
      playbackSource = undefined;
      source.disconnect();
      playbackOffsetSeconds = 0;
      playbackState = "ended";
      playPauseButton.disabled = false;
      playPauseButton.setAttribute("aria-busy", "false");
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play again";
      setStatus("Playback complete.", "success");
    };
    playbackStartedAt = context.currentTime;
    source.start(0, playbackOffsetSeconds);
    playbackState = "playing";
    playPauseButton.disabled = false;
    playPauseButton.setAttribute("aria-busy", "false");
    playPauseButton.setAttribute("aria-pressed", "true");
    playbackIcon.textContent = "❚❚";
    playbackLabel.textContent = "Pause audio";
    setStatus("Playing audio.");
  } catch {
    if (revision !== playbackRevision) return;
    stopPlaybackSource();
    if (playbackContext === context) {
      playbackContext = undefined;
    }
    closePlaybackContext(context);
    if (!playbackBytes && playbackResource) {
      playbackState = "resource";
      playPauseButton.disabled = false;
      playPauseButton.setAttribute("aria-busy", "false");
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play audio";
      setStatus(
        `Playback could not load yet. If file approval is pending, allow it and choose Play audio again. ${fileDeliveryStatus()}`,
        "error",
      );
      return;
    }
    playbackState = previousState === "paused" ? "paused" : previousState === "ended" ? "ended" : "ready";
    playPauseButton.disabled = false;
    playPauseButton.setAttribute("aria-busy", "false");
    playPauseButton.setAttribute("aria-pressed", "false");
    playbackIcon.textContent = "▶";
    playbackLabel.textContent = playbackState === "ended" ? "Play again" : "Play audio";
    const fallback = currentResource ? ` ${fileDeliveryStatus()}` : "";
    setStatus(
      `Playback could not start because Web Audio was unavailable or could not decode the audio.${fallback}`,
      "error",
    );
  }
}

async function downloadCurrentResource() {
  if (isDownloading || !currentResource) return;
  if (!hostCanDownload) {
    setStatus("This host does not support native file downloads.", "error");
    return;
  }

  const resource = currentResource;
  const revision = resultRevision;
  setDownloading(true);
  setStatus("Waiting for download confirmation…");
  try {
    let contents: Parameters<App["downloadFile"]>[0]["contents"] = [resource];
    let loadedFile: Awaited<ReturnType<typeof loadFileResource>> | undefined;
    try {
      loadedFile = await loadFileResource(resource);
    } catch {
      // A ResourceLink remains a portable fallback when inline materialization
      // is unavailable; the host fetches it through the originating MCP server.
    }
    if (loadedFile) {
      const { data, mimeType } = loadedFile;
      verifyFileEnvelope(data, resource, currentMetadata);
      contents = [
        {
          type: "resource",
          resource: {
            uri: `file:///${encodeURIComponent(resource.name)}`,
            mimeType,
            blob: data,
          },
        },
      ];
    }
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    const result = await app.downloadFile({ contents });
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    if (result.isError) {
      setStatus("Download cancelled or declined by the host.");
      return;
    }
    setStatus("Download started successfully.", "success");
  } catch {
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    setStatus("Download failed. The temporary audio file may have expired.", "error");
  } finally {
    if (revision === resultRevision && currentResource?.uri === resource.uri) {
      setDownloading(false);
    }
  }
}

function findMaterializedAudio(value: unknown) {
  const pending: unknown[] = [value];
  const seen = new Set<object>();
  for (let index = 0; index < pending.length && index < 40; index += 1) {
    const candidate = pending[index];
    if (Array.isArray(candidate)) {
      pending.push(...candidate.slice(0, 20));
      continue;
    }
    const record = asRecord(candidate);
    if (!record || seen.has(record)) continue;
    seen.add(record);

    if (record.type === "resource") {
      const resource = asRecord(record.resource);
      const blob = resource?.blob;
      const mimeType = resource?.mimeType;
      const uri = resource?.uri;
      if (
        typeof blob === "string" &&
        typeof mimeType === "string" &&
        typeof uri === "string" &&
        mimeType.toLowerCase().startsWith("audio/")
      ) {
        return {
          content: candidate as EmbeddedResource,
          data: blob,
          mimeType,
        };
      }
    }

    pending.push(
      record.content,
      record.call_tool_result,
      record.mcp_tool_result,
      record.callToolResult,
      record.mcpToolResult,
      record.result,
    );
  }
  return undefined;
}

function resultContainsToolError(value: unknown) {
  const record = asRecord(value);
  return record?.isError === true;
}

function recordMaterializeFailure(error: unknown) {
  const errorType = error instanceof DOMException
    ? error.name
    : error instanceof Error
      ? error.constructor.name
      : typeof error;
  // Host exceptions may contain temporary URLs or opaque identifiers. Keep the
  // diagnostic stable and avoid echoing their contents.
  console.warn(`[VoxBridge] VB-HANDOFF-MATERIALIZE (${errorType})`);
}

async function addCurrentFileToChatGpt() {
  if (isMaterializingForChatGpt || materializedForChatGpt || !currentResource) return;
  if (!hostCanCallTools) {
    setStatus("This host cannot call the audio materialization tool from the App.", "error");
    return;
  }
  if (!hostCanAttachToChatGpt) {
    setStatus("This ChatGPT host cannot add audio files to the message composer.", "error");
    return;
  }

  const resource = currentResource;
  const metadata = currentMetadata;
  const revision = resultRevision;
  setMaterializingForChatGpt(true);
  setStatus("Preparing the exact generated audio file for ChatGPT…");
  try {
    const result = await app.callServerTool({
      name: "materialize_audio_file",
      arguments: { file_name: resource.name },
    });
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    if (resultContainsToolError(result)) {
      throw new Error("The materialization tool returned an error");
    }

    const audio = findMaterializedAudio(result);
    if (!audio) {
      throw new Error("ChatGPT did not return an embedded audio file");
    }
    const bytes = decodeBase64Audio(audio.data, audio.mimeType);
    await verifyFileBytes(bytes, resource, metadata);
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;

    const modelContextSequence = ++modelContextUpdateSequence;
    modelContextResourceUri = String(audio.content.resource.uri);
    await scheduleModelContextUpdate([audio.content], modelContextSequence);
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;

    cachedFileResourceUri = resource.uri;
    cachedFileBase64 = audio.data;
    cachedFileBytes = bytes;
    cachedFileMimeType = audio.mimeType;
    materializedForChatGpt = true;
    setStatus(
      "Added the exact audio file to ChatGPT's composer. Send your next message to share it with the model. No regeneration occurred.",
      "success",
    );
  } catch (error) {
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    materializedForChatGpt = false;
    modelContextResourceUri = undefined;
    updateFileActions(currentResource);
    recordMaterializeFailure(error);
    setStatus(
      "ChatGPT did not add the file. If the temporary VoxBridge file is still valid, retry without regenerating it; use Download when offered.",
      "error",
    );
  } finally {
    if (revision === resultRevision && currentResource?.uri === resource.uri) {
      setMaterializingForChatGpt(false);
    }
  }
}

downloadButton.addEventListener("click", () => {
  void downloadCurrentResource();
});

saveChatGptButton.addEventListener("click", () => {
  void addCurrentFileToChatGpt();
});

playPauseButton.addEventListener("click", () => {
  void togglePlayback();
});

app.addEventListener("toolinput", (params) => {
  const requested = params.arguments?.delivery;
  if (requested === "playback" || requested === "file" || requested === "both") {
    mode.textContent = modeLabel(requested);
  }
  invalidateChatGptAttachment(true);
  resultRevision += 1;
  latestResult = undefined;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
  setDownloading(false);
  actions.hidden = true;
  summary.textContent = "Generating audio…";
  setStatus("Waiting for the audio provider.");
});

app.addEventListener("toolresult", (result) => {
  invalidateChatGptAttachment(true);
  renderResult(result);
  syncChatGptAttachmentFromHostContext(app.getHostContext(), false);
});

app.addEventListener("toolcancelled", (params) => {
  invalidateChatGptAttachment(true);
  resultRevision += 1;
  latestResult = undefined;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
  setDownloading(false);
  actions.hidden = true;
  summary.textContent = "Audio generation was cancelled.";
  setStatus(params.reason ? `Cancelled: ${params.reason}` : "Cancelled.");
});

app.addEventListener("hostcontextchanged", (params) => {
  syncChatGptAttachmentFromHostContext(params, true);
});

app.onteardown = () => {
  invalidateChatGptAttachment(false);
  resultRevision += 1;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
  return {};
};

window.addEventListener("pagehide", () => {
  invalidateChatGptAttachment(false);
  resultRevision += 1;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
});

try {
  await app.connect();
  isConnected = true;
  const hostCapabilities = app.getHostCapabilities();
  hostCanDownload = hostCapabilities?.downloadFile !== undefined;
  hostCanReadResources = hostCapabilities?.serverResources !== undefined;
  hostCanCallTools = hostCapabilities?.serverTools !== undefined;
  const hasChatGptComposerSemantics =
    hostCapabilities?.experimental?.["openai/modelContext"] !== undefined;
  hostCanAttachToChatGpt = Boolean(
    hasChatGptComposerSemantics && hostCapabilities?.updateModelContext?.resource !== undefined,
  );
  setMaterializingForChatGpt(false);
  downloadButton.disabled = !hostCanDownload;
  downloadButton.hidden = !hostCanDownload;
  saveChatGptButton.hidden = true;
  if (latestResult) {
    renderResult(latestResult);
    syncChatGptAttachmentFromHostContext(app.getHostContext(), false);
  } else {
    setStatus(
      hostCanDownload
        ? "Connected. Waiting for generated audio…"
        : "Connected. File results remain retrievable through VoxBridge even when this host omits a native Download control.",
    );
  }
} catch (error) {
  const message = error instanceof Error ? error.message : "Unknown error";
  setStatus(`Could not connect to the host: ${message}`, "error");
}
