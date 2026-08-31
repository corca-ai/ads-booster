const KEY_PATTERN = /^(v[1-9][0-9]*):([A-Za-z0-9+/]+={0,2})$/u;
const NONCE_BYTES = 12;
const KEY_BYTES = 32;

export class ThreadsTokenVaultError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ThreadsTokenVaultError";
    this.code = code;
  }
}

const invalidKey = () => new ThreadsTokenVaultError(
  "THREADS_TOKEN_KEY_INVALID",
  "Threads token encryption key must be a versioned 256-bit key",
);

const decodeBase64 = (value, errorFactory) => {
  try {
    const decoded = atob(value);
    return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
  } catch {
    throw errorFactory();
  }
};

const encodeBase64 = (value) => {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
};

const parseKey = (configuredKey) => {
  if (typeof configuredKey !== "string") throw invalidKey();
  const match = KEY_PATTERN.exec(configuredKey);
  if (!match) throw invalidKey();
  const keyBytes = decodeBase64(match[2], invalidKey);
  if (keyBytes.byteLength !== KEY_BYTES) throw invalidKey();
  return { keyVersion: match[1], keyBytes };
};

const parseRecord = (record) => {
  const recordError = () => new ThreadsTokenVaultError(
    "THREADS_TOKEN_RECORD_INVALID",
    "Threads token ciphertext record is invalid",
  );
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw recordError();
  }
  const fields = Object.keys(record).sort();
  if (
    fields.length !== 3
    || fields[0] !== "ciphertext"
    || fields[1] !== "key_version"
    || fields[2] !== "nonce"
  ) throw recordError();
  const { ciphertext, key_version: keyVersion, nonce } = record;
  if (
    typeof ciphertext !== "string" || ciphertext.length === 0
    || typeof keyVersion !== "string" || keyVersion.length === 0
    || typeof nonce !== "string" || nonce.length === 0
  ) {
    throw recordError();
  }
  const nonceBytes = decodeBase64(nonce, recordError);
  const ciphertextBytes = decodeBase64(ciphertext, recordError);
  if (nonceBytes.byteLength !== NONCE_BYTES || ciphertextBytes.byteLength < 17) {
    throw recordError();
  }
  return { ciphertextBytes, keyVersion, nonceBytes };
};

export function createThreadsTokenVault(configuredKey, options = {}) {
  const { keyVersion, keyBytes } = parseKey(configuredKey);
  const randomBytes = options.randomBytes ?? ((size) => crypto.getRandomValues(new Uint8Array(size)));
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const additionalData = encoder.encode(`threads-token:${keyVersion}`);
  let cryptoKey;

  const getKey = async () => {
    cryptoKey ??= crypto.subtle.importKey(
      "raw",
      keyBytes,
      { name: "AES-GCM" },
      false,
      ["encrypt", "decrypt"],
    );
    return cryptoKey;
  };

  return Object.freeze({
    keyVersion,

    async encrypt(token) {
      if (typeof token !== "string" || token.length === 0) {
        throw new ThreadsTokenVaultError(
          "THREADS_TOKEN_PLAINTEXT_INVALID",
          "Threads access token must be a non-empty string",
        );
      }
      const nonceBytes = randomBytes(NONCE_BYTES);
      if (!(nonceBytes instanceof Uint8Array) || nonceBytes.byteLength !== NONCE_BYTES) {
        throw new ThreadsTokenVaultError(
          "THREADS_TOKEN_RANDOM_INVALID",
          "Threads token nonce source returned an invalid value",
        );
      }
      const ciphertext = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv: nonceBytes, additionalData },
        await getKey(),
        encoder.encode(token),
      );
      return Object.freeze({
        ciphertext: encodeBase64(new Uint8Array(ciphertext)),
        key_version: keyVersion,
        nonce: encodeBase64(nonceBytes),
      });
    },

    async decrypt(record) {
      const parsed = parseRecord(record);
      if (parsed.keyVersion !== keyVersion) {
        throw new ThreadsTokenVaultError(
          "THREADS_TOKEN_KEY_VERSION_MISMATCH",
          "Threads token ciphertext uses a different key version",
        );
      }
      try {
        const plaintext = await crypto.subtle.decrypt(
          { name: "AES-GCM", iv: parsed.nonceBytes, additionalData },
          await getKey(),
          parsed.ciphertextBytes,
        );
        return decoder.decode(plaintext);
      } catch {
        throw new ThreadsTokenVaultError(
          "THREADS_TOKEN_DECRYPT_FAILED",
          "Threads token ciphertext could not be decrypted",
        );
      }
    },
  });
}

export function createThreadsTokenVaultFromEnv(env, options = {}) {
  return createThreadsTokenVault(env?.THREADS_TOKEN_ENCRYPTION_KEY, options);
}
