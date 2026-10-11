import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

import { api } from "./api";
import type { Passkey, User } from "./types";

interface Ceremony<T> {
  ceremony_id: string;
  public_key: T;
}

export async function loginWithPasskey(): Promise<User> {
  const options = await api<Ceremony<PublicKeyCredentialRequestOptionsJSON>>(
    "/v1/auth/passkeys/authentication/options",
    { method: "POST" },
  );
  const credential = await startAuthentication({ optionsJSON: options.public_key });
  return api<User>("/v1/auth/passkeys/authentication/verify", {
    method: "POST",
    body: { ceremony_id: options.ceremony_id, credential },
  });
}

/** With an enrollment token (first device / recovery) or from the current session. */
export async function registerPasskey(label: string, enrollmentToken?: string): Promise<Passkey> {
  const options = await api<Ceremony<PublicKeyCredentialCreationOptionsJSON>>(
    "/v1/auth/passkeys/registration/options",
    { method: "POST", body: enrollmentToken ? { enrollment_token: enrollmentToken } : {} },
  );
  const credential = await startRegistration({ optionsJSON: options.public_key });
  return api<Passkey>("/v1/auth/passkeys/registration/verify", {
    method: "POST",
    body: { ceremony_id: options.ceremony_id, credential, label },
  });
}
