/**
 * Files shared to LifeOS from the phone's share sheet (manifest `share_target`, ADR 0022).
 * The service worker keeps them in memory and hands them to the documents page, which
 * asks once; the owner then chooses the type and the space and sends them.
 */

/** Query parameter of the documents page: `1` (files waiting in the worker) or `indisponible`. */
export const SHARE_PARAM = "partage";
export const SHARE_READY = "1";
export const SHARE_FAILED = "indisponible";
/** Where /partage sends the browser when no service worker took the files. */
export const SHARE_PAGE_FAILED = `/documents?${SHARE_PARAM}=${SHARE_FAILED}`;
/** Types offered in the share sheet: those the documents upload accepts (lib/documents.ts). */
export const SHARE_ACCEPT = [
  "application/pdf",
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
  "text/plain",
  ".pdf",
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".heic",
  ".heif",
  ".txt",
];

const SHARE_TIMEOUT_MS = 10_000;

interface ShareMessage {
  type: "lifeos-share";
  files: File[];
}

function isShareMessage(data: unknown): data is ShareMessage {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as { type?: unknown }).type === "lifeos-share" &&
    Array.isArray((data as { files?: unknown }).files)
  );
}

/**
 * Asks the service worker for the shared files. Null when there is no worker or it does not
 * answer in time; an empty list when it holds nothing (already handed over, or expired).
 */
export function receiveSharedFiles(
  container: ServiceWorkerContainer | undefined = typeof navigator === "undefined" ? undefined : navigator.serviceWorker,
  timeoutMs = SHARE_TIMEOUT_MS,
): Promise<File[] | null> {
  if (!container) return Promise.resolve(null);
  return new Promise((resolve) => {
    const finish = (files: File[] | null) => {
      clearTimeout(timer);
      container.removeEventListener("message", onMessage);
      resolve(files);
    };
    const onMessage = (event: MessageEvent) => {
      if (isShareMessage(event.data)) finish(event.data.files.filter((file) => file instanceof Blob));
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    container.addEventListener("message", onMessage);
    container.startMessages?.();
    void container.ready
      .then((registration) => {
        const worker = registration.active ?? container.controller;
        if (!worker) return finish(null);
        worker.postMessage({ type: "lifeos-share-request" });
      })
      .catch(() => finish(null));
  });
}

/** What the page says above the upload form, for the shared file `index` of `count`. */
export function sharedFileNotice(index: number, count: number): string {
  const which = count > 1 ? `Fichier partagé ${index + 1} sur ${count}` : "Fichier partagé";
  return `${which} : choisissez son type (document ou ticket de caisse) et son espace, puis envoyez-le.`;
}
