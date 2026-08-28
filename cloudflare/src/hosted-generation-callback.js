import { applyHostedGenerationResult } from "./hosted-workspace.js";
import { HttpError } from "./http-error.js";
import { assertHostedCallbackTransport, reserveWorkerTaskCallback } from "./mac-workers.js";

/**
 * Take one caption batch back from the Mac worker that wrote it.
 *
 * The image callback owns an artifact and a candidate's state machine; this one owns rows
 * that do not exist yet, so the ordering is the other way round: reserve the callback first,
 * write the candidates, then close the task. A retry of the same reservation lands on the
 * batch id check inside `applyHostedGenerationResult` and writes nothing twice.
 */
export async function receiveHostedGenerationCallback(env, task, callback, worker = null) {
  assertHostedCallbackTransport(task, worker);
  if (
    task.run_id !== callback.run_id ||
    task.account_id !== callback.account_id ||
    callback.kind !== "generate_candidates"
  ) {
    throw new HttpError(409, "callback scope does not match hosted generation task");
  }
  if (callback.callback_id !== `${callback.task_id}:completed`) {
    throw new HttpError(409, "callback_id does not match hosted generation task");
  }
  const status = callback.result?.status;
  if (!["succeeded", "failed", "unknown_side_effect"].includes(status)) {
    throw new HttpError(400, "invalid hosted generation result status");
  }
  const storedResultJson = JSON.stringify(callback.result);
  if (task.callback_id) {
    if (task.callback_id !== callback.callback_id) throw new HttpError(409, "conflicting callback");
    if (task.result_json !== storedResultJson) throw new HttpError(409, "callback result changed");
    return { accepted: true, duplicate: true };
  }
  if (worker) {
    const reservation = await reserveWorkerTaskCallback(
      env.DB, worker, task, callback.callback_id, storedResultJson,
    );
    if (reservation.duplicate) return { accepted: true, duplicate: true };
  }
  let created = 0;
  if (status === "succeeded") {
    let profileId = null;
    try {
      profileId = JSON.parse(task.task_json)?.payload?.context_profile_id ?? null;
    } catch {
      profileId = null;
    }
    // A batch that produced three of four is three worth keeping, so a partial result is
    // stored and the shortfall is reported through the task row rather than thrown away.
    const applied = await applyHostedGenerationResult(
      env,
      {
        task_id: task.task_id,
        account_id: task.account_id,
        persona_id: task.persona_id ?? null,
        context_profile_id: profileId,
      },
      callback.result?.output,
    );
    created = applied.created;
  }
  const now = new Date().toISOString();
  const completion = worker
    ? env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL AND worker_id = ? AND lease_id = ?
         AND callback_reservation_id = ?`,
    ).bind(
      status, storedResultJson, callback.callback_id, now, task.task_id,
      worker.worker_id, task.lease_id, callback.callback_id,
    )
    : env.DB.prepare(
      `UPDATE hosted_workspace_capture_tasks
       SET state = ?, result_json = ?, callback_id = ?, updated_at = ?
       WHERE task_id = ? AND callback_id IS NULL`,
    ).bind(status, storedResultJson, callback.callback_id, now, task.task_id);
  const updated = await completion.run();
  if (updated.meta.changes !== 1) {
    const winner = await env.DB.prepare(
      "SELECT callback_id, result_json FROM hosted_workspace_capture_tasks WHERE task_id = ?",
    )
      .bind(task.task_id)
      .first();
    if (winner?.callback_id !== callback.callback_id || winner.result_json !== storedResultJson) {
      throw new HttpError(409, "conflicting hosted generation callback");
    }
    return { accepted: true, duplicate: true, created };
  }
  return { accepted: true, duplicate: false, created };
}
