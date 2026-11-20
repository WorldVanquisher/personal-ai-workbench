# PAI 1.0 acceptance checklist (proposal)

Each line is a real user workflow, the command that proves it, and the evidence that must exist
for the release candidate commit. "Auto" evidence comes from `python3 -B tools/verify.py
--require-all` (logs and `verify-report.json` in its work folder); "owner" evidence is a recorded
manual run. A box is ticked only from a run of the candidate itself, never from an earlier one.

## 1. First win, no account

- [ ] `pai guide` completes plan -> dispatch -> delivered -> handoff with the fake worker, from a
      fresh HOME, with no network. Auto: `test_guide` (under `unshare -rn` where available).
- [ ] `pai guide --recovery` shows a crash, a recovery preview, and a recovered, completed task.
      Auto: `test_guide.test_the_recovery_walkthrough_offline`.
- [ ] `pai guide --cleanup DIR` removes only a folder `pai guide` made. Auto: `test_guide`.

## 2. Install, upgrade, remove

- [ ] Wheel install in a clean environment finds its workbench and runs `pai guide`. Auto:
      `package` step (081, 082).
- [ ] Upgrade from the previous release reads its config, notes and runs without writing, and
      the archive preview writes nothing. Auto: `package` (083).
- [ ] An interrupted install leaves a working launcher. Auto: `package` (084).
- [ ] Uninstall keeps configuration, notes and run state. Auto: `package` (085).
- [ ] Wheel, archive and version agree byte for byte. Auto: `package` (086), `bundle`.
- [ ] CI is green on Linux, macOS and Windows for the candidate. Owner (CI run).

## 3. Set up a real project

- [ ] `pai doctor` separates ESSENTIAL from OPTIONAL and prints copyable repairs for this
      platform; exit 1 only for an essential gap. Auto: `test_doctor`.
- [ ] `pai init` never overwrites without `--update`. Auto: `test_workbench.Init`.
- [ ] `pai task new` previews; `--write` never overwrites. Auto: `test_workbench`.

## 4. Run agents, with a person in control

- [ ] `pai run --dry-run` changes nothing and names each lane's evidence sources. Auto:
      `test_workbench`, `test_backends`.
- [ ] A folder-trust prompt is never answered or typed into; `pai trust` hands it to you. Auto:
      `test_dispatch`, `test_recovery.test_trust_is_a_human_action_with_its_folder`.
- [ ] A task is `delivered` only on the agent's own transcript evidence. Auto: `test_dispatch`,
      `test_backends.test_missing_transcript_is_unknown_never_delivered`.
- [ ] One real Claude lane: trust, deliver, crash and recover without a resend. **Owner**
      (REAL-AGENT-SMOKE.md).

## 5. Understand and recover

- [ ] `pai status` names a lifecycle and why for every lane, says PARTIAL delivery plainly, and
      fits 40 columns. Auto: `test_runstate`.
- [ ] `pai inspect LANE` names the file or command behind each piece of evidence. Auto:
      `test_runstate`.
- [ ] `pai recover` previews first; an unknown dispatch waits for `--choose`; decisions are
      logged with before- and after-state; two recoveries never both act. Auto: `test_recovery`.

## 6. Move work between places

- [ ] `pai task export` / `import`: deterministic, UTF-8, private fields apart, credentials
      flagged, no overwrite, queue recreated. Auto: `test_cards`, `test_portable_cards`.

## 7. Work in the browser

- [ ] `pai edit FILE.md`: one-time link, confirmed save, stale refusal. Auto: `test_mdserver`,
      packaging edit session.
- [ ] `pai web`: the same lifecycle names as `pai status`, inspect, resume preview, paste preview,
      staged vs submitted; loopback, session and CSRF on every endpoint. Auto: `test_console`,
      `console` step.
- [ ] Keyboard focus, status announcements and a 390 px screen in a real browser. **Owner**
      (browser kit) - required only if `pai web` is promoted from experimental.

## 8. Review and publish (owner's boundary)

- [ ] `pai pr draft` renders the owner page and leaves index and refs unchanged; nothing is
      published; AI trailers and private text removed, human co-authors kept. Auto: `test_draft`.
- [ ] `pai pr open` is the only publishing command and runs only when the owner runs it. Auto:
      `test_review`, `test_draft.Defaults`.

## 9. Release evidence

- [ ] `tools/verify.py --require-all` passes on the candidate (quickstart needs uv).
- [ ] PLATFORMS.md criteria met for every platform claim in the release notes.
- [ ] Release notes list stable / experimental / unverified (CAPABILITIES.md) and the migration
      notes (MIGRATION.md).
- [ ] Tagging and releasing: **owner decision**, separate from this checklist.
