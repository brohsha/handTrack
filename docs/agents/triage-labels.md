# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## All five labels exist on the repo

`wontfix` came with GitHub's stock label set; the other four were created during setup. Nothing needs creating before `/triage` runs.

Verify with:

```sh
gh label list --repo brohsha/handTrack
```

If one ever goes missing, recreate it:

```sh
gh label create needs-triage    --repo brohsha/handTrack --color d4c5f9 --description "Maintainer needs to evaluate this issue"
gh label create needs-info      --repo brohsha/handTrack --color fbca04 --description "Waiting on reporter for more information"
gh label create ready-for-agent --repo brohsha/handTrack --color 0e8a16 --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --repo brohsha/handTrack --color 1d76db --description "Requires human implementation"
```
