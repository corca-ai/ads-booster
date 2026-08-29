/**
 * The control plane's request error, shared by every module that answers one.
 *
 * It lived inside `index.js`, which imports `cloudflare:workers` and therefore cannot be
 * loaded by `node --test`. Anything that throws it was untestable by association; moving the
 * class here is what lets a callback handler live in a module a test can import.
 */
export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
