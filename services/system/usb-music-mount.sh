#!/bin/bash
# Auto-mount/unmount NTFS USB drives and expose via Samba for Sonos
# Called by udev rule 99-usb-music.rules

MOUNT_ROOT="/mnt/usb-music"
LOG_TAG="usb-music"

case "$1" in
    add)
        DEV="$2"
        [ -z "$DEV" ] && exit 1

        # Only handle NTFS
        FSTYPE=$(blkid -o value -s TYPE "$DEV" 2>/dev/null)
        if [ "$FSTYPE" != "ntfs" ]; then
            logger -t "$LOG_TAG" "Skipping $DEV (type: $FSTYPE, not NTFS)"
            exit 0
        fi

        # Use volume label if available, otherwise device name
        LABEL=$(blkid -o value -s LABEL "$DEV" 2>/dev/null)
        LABEL="${LABEL:-$(basename "$DEV")}"
        # Sanitize label for use as directory name
        LABEL=$(echo "$LABEL" | tr -cs 'A-Za-z0-9_-' '_' | sed 's/_$//')
        MOUNTPOINT="$MOUNT_ROOT/$LABEL"

        # Skip if already mounted
        if mountpoint -q "$MOUNTPOINT" 2>/dev/null; then
            logger -t "$LOG_TAG" "$DEV already mounted at $MOUNTPOINT"
            exit 0
        fi

        mkdir -p "$MOUNTPOINT"

        # Try kernel ntfs3 first, fall back to ntfs-3g
        if mount -t ntfs3 -o ro,uid=1000,gid=1000 "$DEV" "$MOUNTPOINT" 2>/dev/null; then
            logger -t "$LOG_TAG" "Mounted $DEV at $MOUNTPOINT (ntfs3, label: $LABEL)"
        elif mount -t ntfs-3g -o ro,uid=1000,gid=1000 "$DEV" "$MOUNTPOINT" 2>/dev/null; then
            logger -t "$LOG_TAG" "Mounted $DEV at $MOUNTPOINT (ntfs-3g, label: $LABEL)"
        else
            logger -t "$LOG_TAG" "Failed to mount $DEV"
            rmdir "$MOUNTPOINT" 2>/dev/null
            exit 1
        fi
        ;;

    remove)
        # Lazy-unmount anything under our root whose backing device is gone
        for mp in "$MOUNT_ROOT"/*/; do
            [ -d "$mp" ] || continue
            if mountpoint -q "$mp"; then
                SRC=$(findmnt -n -o SOURCE "$mp" 2>/dev/null)
                if [ ! -b "$SRC" ]; then
                    umount -l "$mp" 2>/dev/null
                    rmdir "$mp" 2>/dev/null
                    logger -t "$LOG_TAG" "Unmounted $mp (device removed)"
                fi
            else
                # Stale empty directory
                rmdir "$mp" 2>/dev/null
            fi
        done
        ;;
esac
