#!/bin/sh
set -eu

server="${SMB_SERVER:-samba}"
share="${SMB_SHARE:-atlanticus}"
mount_path="${SMB_MOUNT_PATH:-/mnt/atlanticus-smb}"
mount_options="${SMB_MOUNT_OPTIONS:-guest,vers=3.1.1,cache=none,actimeo=0,nosharesock,file_mode=0660,dir_mode=0770}"
probe=/workspace/backend/runtime/tests/smb/probe.py

mkdir -p "$mount_path"
attempt=0
while ! mount -t cifs "//$server/$share" "$mount_path" -o "$mount_options"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "SMB mount did not become available after 30 attempts; verify privileged Docker mounts and host CIFS kernel support" >&2
        exit 2
    fi
    sleep 1
done

cleanup() {
    umount "$mount_path" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

mount_info="$(findmnt -n -o FSTYPE,OPTIONS --target "$mount_path")"
case "$mount_info" in
    cifs\ *) ;;
    *)
        echo "Expected a CIFS mount, found: $mount_info" >&2
        exit 3
        ;;
esac
case ",$mount_info," in
    *,nobrl,*)
        echo "SMB fencing probe refuses mounts using nobrl because remote byte-range locking is disabled" >&2
        exit 4
        ;;
esac

exec /workspace/backend/.venv/bin/python "$probe" "$@" --shared-volume "$mount_path"
