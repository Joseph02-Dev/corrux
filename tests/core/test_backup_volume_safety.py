"""Garde-fous du choix du support de sauvegarde — jamais le disque système.

Régression constatée sur une vraie installation (Ubuntu 26.04) : la
racine est montée sur une partition (/dev/sda2) ou un volume LVM, jamais
sur le disque entier ; l'ancienne comparaison `/dev/sda == racine` ne
l'excluait donc jamais, et le disque système était proposé au formatage.
Arborescences lsblk reproduites telles que produites par `lsblk -J`.
"""

import json
import subprocess

import pytest

from ops.setup_service import SetupError, detect_candidate_volumes, ensure_device_not_in_use

EXTERNAL_EMPTY_DISK = {
    "name": "sdb", "size": "111.8G", "fstype": None, "mountpoint": None, "type": "disk",
}

ROOT_ON_PARTITION = [
    {
        "name": "sda", "size": "232.9G", "fstype": None, "mountpoint": None, "type": "disk",
        "children": [
            {"name": "sda1", "size": "1G", "fstype": "vfat", "mountpoint": "/boot/efi",
             "type": "part"},
            {"name": "sda2", "size": "231.9G", "fstype": "ext4", "mountpoint": "/",
             "type": "part"},
        ],
    },
    EXTERNAL_EMPTY_DISK,
]

ROOT_ON_LVM = [
    {
        "name": "nvme0n1", "size": "476.9G", "fstype": None, "mountpoint": None,
        "type": "disk",
        "children": [
            {"name": "nvme0n1p1", "size": "1G", "fstype": "vfat", "mountpoint": "/boot/efi",
             "type": "part"},
            {"name": "nvme0n1p3", "size": "474G", "fstype": "LVM2_member", "mountpoint": None,
             "type": "part",
             "children": [
                 {"name": "ubuntu--vg-ubuntu--lv", "size": "100G", "fstype": "ext4",
                  "mountpoint": "/", "type": "lvm"},
             ]},
        ],
    },
    EXTERNAL_EMPTY_DISK,
]

SWAP_ONLY_DISK = [
    {
        "name": "sdc", "size": "16G", "fstype": None, "mountpoint": None, "type": "disk",
        "children": [
            {"name": "sdc1", "size": "16G", "fstype": "swap", "mountpoint": "[SWAP]",
             "type": "part"},
        ],
    },
    EXTERNAL_EMPTY_DISK,
]


def _runner_for(blockdevices, root_source):
    def runner(command, **kwargs):
        if command[0] == "findmnt":
            return subprocess.CompletedProcess(command, 0, root_source + "\n", "")
        if command[0] == "lsblk":
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"blockdevices": blockdevices}), ""
            )
        raise AssertionError(f"commande inattendue : {command}")
    return runner


@pytest.mark.parametrize(
    ("tree", "root_source"),
    [
        (ROOT_ON_PARTITION, "/dev/sda2"),
        (ROOT_ON_LVM, "/dev/mapper/ubuntu--vg-ubuntu--lv"),
        (SWAP_ONLY_DISK, "/dev/sdb9"),
    ],
    ids=["racine-sur-partition", "racine-sur-lvm", "disque-de-swap"],
)
def test_only_the_unused_disk_is_offered(tree, root_source):
    candidates = detect_candidate_volumes(runner=_runner_for(tree, root_source))

    assert [c.device_path for c in candidates] == ["/dev/sdb"]


@pytest.mark.parametrize("device", ["/dev/sda", "/dev/sda1", "/dev/sda2"])
def test_system_disk_and_its_partitions_are_refused(device):
    runner = _runner_for(ROOT_ON_PARTITION, "/dev/sda2")

    with pytest.raises(SetupError, match="disque système"):
        ensure_device_not_in_use(device, runner=runner)


def test_lvm_backed_system_disk_is_refused():
    runner = _runner_for(ROOT_ON_LVM, "/dev/mapper/ubuntu--vg-ubuntu--lv")

    with pytest.raises(SetupError):
        ensure_device_not_in_use("/dev/nvme0n1", runner=runner)


def test_unused_external_disk_is_accepted():
    ensure_device_not_in_use("/dev/sdb", runner=_runner_for(ROOT_ON_PARTITION, "/dev/sda2"))
