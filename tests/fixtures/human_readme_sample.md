# backup-thing

Quick script I wrote to back up my home dir to an external drive. Nothing fancy.

I got tired of forgetting to copy stuff before reinstalling, so this just rsyncs the important folders and tars them up with a timestamp. Tested on my laptop running Ubuntu 22.04. Probably works on macOS too but I haven't tried.

## Usage

Run it like:

```
./backup.sh /mnt/external
```

You need write perms on the target. If the target dir doesn't exist it'll bail out instead of creating one, becuase I've been burned by that before.

There's a `--dry-run` flag if you want to see what it would do without actually copying anything. I use this a lot when I change the include list.

The script reads `~/.backup-ignore` for stuff to skip. Mine has things like node_modules, .cache, the usual suspects. One per line, glob patterns are fine.

## Notes

If you recieve a "permission denied" on some random file, that's usually a stale socket or a Snap mount. The script logs them but keeps going. I figured failing on those would be more annoying than helpful.

I don't bother with encryption here teh external drive lives in a locked drawer and that's good enough for my threat model. If you need encryption, pipe through gpg or use restic instead, which is what I should probably switch to eventually.

Restore is just `tar -xzf` into wherever. No magic.

Things I might add later: a flag to prune old backups, slack notifications when it's done, maybe a systemd timer unit so I don't have to remember to run it. None of these are urgent.

PRs welcome but honestly this is a personal tool, don't expect a polished maintainer experience.
