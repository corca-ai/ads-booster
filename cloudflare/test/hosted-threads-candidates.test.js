import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  candidateThreadsProfileSnapshot,
  canSetCandidateThreadsProfile,
} from "../src/hosted-workspace.js";

test("morning and evening candidates snapshot the default regardless of source", () => {
  assert.equal(candidateThreadsProfileSnapshot("morning", "profile-a"), "profile-a");
  assert.equal(candidateThreadsProfileSnapshot("evening", "profile-a"), "profile-a");
  assert.equal(candidateThreadsProfileSnapshot("morning", null), null);
  assert.equal(candidateThreadsProfileSnapshot("manual", "profile-a"), null);
});

test("target profile remains mutable only before accepted image review", () => {
  assert.equal(canSetCandidateThreadsProfile("awaiting_review"), true);
  assert.equal(canSetCandidateThreadsProfile("caption_approved"), true);
  assert.equal(canSetCandidateThreadsProfile("image_awaiting_review"), true);
  assert.equal(canSetCandidateThreadsProfile("submitted"), false);
  assert.equal(canSetCandidateThreadsProfile("rejected"), false);
});

test("candidate profile override is privileged, scoped, active, and revision-bound", async () => {
  const source = await readFile(
    new URL("../src/hosted-workspace.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /action === "threads-profile"[\s\S]*authorizeHostedOperation/u);
  assert.match(source, /WHERE account_id = \? AND profile_id = \? AND state = 'active'/u);
  assert.match(
    source,
    /SET threads_profile_id = \?, revision = revision \+ 1[\s\S]*candidate_id = \? AND revision = \?/u,
  );
  assert.match(source, /threads_profile_username/u);
  assert.match(source, /threads_profile_state/u);
});
