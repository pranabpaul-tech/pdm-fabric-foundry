# artifacts/teams/

Nothing to author here up front. Foundry's native `microsoft365/publish` flow
(`foundry/publish_teams.py`) compiles and submits the Teams app manifest for
you — see step "What happens when you publish?" in Microsoft's publish-to-Teams
docs.

If you ever need the manifest as a standalone `.zip` (e.g. to sideload
manually via **Download & customize** instead of the direct-publish API), the
Foundry portal's Publish dialog has a **Download ZIP** button that produces
one. Drop it here if you do, but don't hand-author one from scratch — its
schema is Foundry's to generate, not ours to guess.
