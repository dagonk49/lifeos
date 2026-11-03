/**
 * Session loss: where to send the owner to sign in again, and where to bring them back.
 * Only same-origin paths are ever followed (an open redirect would let a link send the owner
 * to another site right after a genuine sign-in).
 */

/** Pages reachable without a session: an `unauthenticated` answer there is expected. */
const PUBLIC_PAGES = ["/login", "/enroll"];

export function isPublicPage(pathname: string): boolean {
  return PUBLIC_PAGES.some((page) => pathname === page || pathname.startsWith(`${page}/`));
}

/**
 * The page to open after signing in: `next` when it is a path of this site, else the home
 * page. Refused: absolute URLs, scheme-relative (`//host`) and backslash forms (`/\host`,
 * read as `//host` by browsers), control characters, and the public pages themselves.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (/[\u0000-\u001f\u007f\\]/.test(next)) return "/";
  let url: URL;
  try {
    url = new URL(next, "https://lifeos.invalid");
  } catch {
    return "/";
  }
  if (url.origin !== "https://lifeos.invalid" || isPublicPage(url.pathname)) return "/";
  return `${url.pathname}${url.search}${url.hash}`;
}

/** The login page, asked to come back to `pathname` + `search` (none for the home page). */
export function loginPath(pathname: string, search = ""): string {
  const back = safeNext(`${pathname}${search}`);
  return back === "/" ? "/login" : `/login?next=${encodeURIComponent(back)}`;
}

let signingOut = false;

/**
 * Set while the owner signs out (Déconnexion): the requests still running are answered
 * `unauthenticated` and must not turn the sign-out into a "come back here" sign-in. The
 * login page resets it once shown.
 */
export function setSigningOut(value: boolean): void {
  signingOut = value;
}

/**
 * Opens the login page after a lost session, asked to come back to the current page;
 * nothing on the public pages (an `unauthenticated` answer is expected there) or while
 * signing out. Returns whether it navigated.
 */
export function redirectToLogin(navigate: (path: string) => void): boolean {
  if (signingOut) return false;
  const { pathname, search } = window.location;
  if (isPublicPage(pathname)) return false;
  navigate(loginPath(pathname, search));
  return true;
}

/** Keys of the drafts kept in sessionStorage (this tab only) until sent. */
export const DRAFT_KEYS = { chat: "lifeos.draft.chat", journal: "lifeos.draft.journal" } as const;

/** Forgets every draft (sign-out: the next person on this computer must not see them). */
export function clearDrafts(): void {
  for (const key of Object.values(DRAFT_KEYS)) {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Storage disabled: nothing was kept.
    }
  }
}
