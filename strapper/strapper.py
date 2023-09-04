import rich.traceback

rich.traceback.install(show_locals=True)

import oreo
import orjson as json
import os
import yaml

from addict import Dict
from bakery import (
    getconf,
    mkswap,
    mount,
    nix,
    nixos_generate_config,
    nixos_install,
    nixos_rebuild,
    parted,
    rsync,
    sd,
    swapon,
    umount,
    zfs,
    zpool,
)
from functools import partial
from importlib.resources import files
from pathlib import Path
from rich.prompt import Prompt
from rich import print

import click

# Adapted From:
# Answer: https://stackoverflow.com/a/58941536/10827766
# User: https://stackoverflow.com/users/674039/wim
strapper_resources = files("strapper.resources")


def update_datasets(
    ctx,
    swap=0,
    encrypted=False,
    deduplicated=False,
    pool=False,
    root_device=None,
    reserved_only=False,
):
    host = ctx.obj.host
    resources = ctx.obj.resources
    snap_dir = ["snapdir=visible"]
    extra_copies = snap_dir + ["copies=3"]
    cache = ["sync=disabled"]
    reserved = "reserved"
    datasets = Dict(
        yaml.safe_load((strapper_resources / "datasets.yaml").read_text().strip())
    )
    datasets |= yaml.safe_load(
        (strapper_resources / "user_datasets.yaml").read_text().strip()
    )
    primary_user = (strapper_resources / "username.txt").read_text().strip()
    users = Dict(json.loads((strapper_resources / "users.json").read_text().strip()))
    homes = Dict(json.loads((strapper_resources / "homes.json").read_text().strip()))
    datasets[host].datasets.jails.datasets.base = (dict(),)
    datasets[host].options = ["mountpoint=legacy"]
    for user in users.values():
        datasets.system.datasets.home.datasets[user] = dict()
        datasets.system.datasets.persist.datasets[user] = dict()
        datasets.virt.datasets.podman.datasets[user] = dict()
    if reserved_only:
        zfs.create(host + "/" + reserved, o="mountpoint=none")
    else:
        with open(resources + "/datasets.nix", "w") as dnix:
            dnix.write(
                "".join(
                    (
                        'host: { \n\t"',
                        (root_device or "${host}/system/root"),
                        '" = "/";\n',
                    )
                )
            )

            def recurse(ddict, dname, droot, mountpoint=""):
                _datasets = zfs.list(
                    r=True,
                    o="name",
                    _list=True,
                    _ignore_stderr=True,
                )
                _datasets = _datasets[2 : len(_datasets)]
                _dataset = droot + "/" + dname
                _real_dataset = _dataset.replace("${host}", host)
                cloning = dname != "base" and (encrypted and deduplicated)
                prefixes = (
                    "system",
                    "system/root",
                    "swap",
                    "base",
                    "omniverse",
                    reserved,
                )
                if cloning:
                    clone_or_create = "clone"
                    snapshot_or_none = host + "/base@root"
                else:
                    clone_or_create = "create"
                    snapshot_or_none = ""
                if not _real_dataset in [host + "/" + prefix for prefix in prefixes]:
                    if _mountpoint := ddict.get("mountpoint", ""):
                        mountpoint = _mountpoint
                    else:
                        if mountpoint:
                            mountpoint = mountpoint + "/" + dname
                            _mountpoint = mountpoint
                        else:
                            _mountpoint = _dataset.removeprefix("${host}" + "/")
                            for prefix in prefixes:
                                _mountpoint = _mountpoint.removeprefix(prefix + "/")
                            _mountpoint = "/" + _mountpoint
                    if _real_dataset.startswith(
                        host_user := host + "/" + primary_user
                    ) and (not (_real_dataset == host_user)):
                        dnix.write(
                            "".join(
                                (
                                    '\t"',
                                    _dataset,
                                    '" = [ ',
                                    " ".join(
                                        (
                                            '"' + homes[user] + "/" + dname + '"'
                                            for user in users.keys()
                                        )
                                    ),
                                    " ];\n",
                                )
                            )
                        )

                    # TODO: What does this do? Mind that this sits in the middle of an if statement.
                    # else:
                    #     for user in users.keys():
                    #         dnix.write(
                    #             "".join(
                    #                 '\t"',
                    #                 _dataset,
                    #                 '" = "',
                    #                 homes[user] + "/" + dname,
                    #                 '";\n',
                    #             )
                    #         )

                    else:
                        dnix.write(
                            "".join('\t"', _dataset, '" = "', _mountpoint, '";\n')
                        )
                if pool and (not _real_dataset in _datasets):
                    zfs(
                        snapshot_or_none,
                        _real_dataset,
                        _subcommand=clone_or_create,
                        o={"repeat-with-values": ddict.get("options", [])},
                    )
                    zfs.snapshot(_real_dataset + "@blank", r=True)
                    zfs.hold("blank", _real_dataset + "@blank", r=True)
                for [key, value] in ddict.get("datasets", Dict()).items():
                    recurse(value, key, _dataset, mountpoint)

            for [key, value] in datasets.items():
                recurse(value, key, "${host}")
            dnix.write("}")
    if pool or reserved_only:
        pool_size_plus_metric = zpool.get(
            "size", host, H=True, _list=True, _split=True
        )[2]
        pool_size = round(float(pool_size_plus_metric[0:-1]), 2)
        pool_metric = pool_size_plus_metric[-1]

        def pool_percentage_value(percentage):
            return str(round(float(percentage) / 100, 2)) + pool_metric

        zfs.set("refreservation=" + pool_percentage_value(15), host + "/" + reserved)
        if not reserved_only and swap:
            swoptions = [
                "com.sun:auto-snapshot=false",
                "compression=zle",
                "logbias=throughput",
                "primarycache=metadata",
                "secondarycache=none",
                "sync=standard",
            ]
            page_size = getconf("PAGESIZE", _str=True)
            zfs.create(
                host + "/swap",
                V=str(swap) + "G",
                b=page_size,
                o={"repeat-with-values": swoptions},
            )
            mkswap("/dev/zvol" + host + "/swap")


@click.group(no_args_is_help=True)
@click.option("-d", "--dazzle", is_flag=True)
@click.option("-H", "--host", required=True)
@click.option("-i", "--inspect", is_flag=True)
@click.option("-P", "--print-run", is_flag=True, cls=oreo.Option, xor=["print"])
@click.option("-p", "--print", is_flag=True, cls=oreo.Option, xor=["print-run"])
@click.option("-r", "--resources-dir")
@click.pass_context
def strapper(ctx, dazzle, host, inspect, print_run, print, resources_dir):
    if os.geteuid() != 0:
        raise SystemError("Sorry; this program needs to be run as root!")
    ctx.ensure_object(dict)
    if resources_dir:
        ctx.obj.resources = resources_dir
    else:
        cwd = Path.cwd()
        nixos_dir = Path("etc/nixos/")
        etc_nixos_dir = Path("/", nixos_dir)
        ctx.obj.resources = cwd / nixos_dir
        if ctx.obj.resources.match(f"*{etc_nixos_dir}"):
            ctx.obj.resources = cwd
        else:
            while not ctx.obj.resources.exists():
                cwd = cwd.parent
                ctx.obj.resources = cwd / nixos_dir
            else:
                if (
                    ctx.obj.resources == etc_nixos_dir
                    and (mnt_dir := ("/mnt" / nixos_dir)).exists()
                ):
                    ctx.obj.resources = mnt_dir
    ctx.obj.host = host
    return getconf.bake_all_(
        _dazzle=dazzle,
        _print_command_and_run=print_run,
        _print_command=print,
        _debug=inspect,
    )


@strapper.command(
    no_args_is_help=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("program-arguments", nargs=-1)
@click.option("-a", "--all", is_flag=True)
@click.option("-c", "--copy", is_flag=True)
@click.option("-g", "--generate", is_flag=True)
@click.option("-i", "--install", is_flag=True)
@click.option(
    "-b",
    "--install-bootloader",
    is_flag=True,
    cls=oreo.Option,
    req_one_of=["install", "all"],
)
@click.option("-r", "--replace", is_flag=True)
@click.option("-R", "--rebuild")
@click.pass_context
def main(
    ctx,
    all,
    copy,
    generate,
    install,
    program_arguments,
    rebuild,
    replace,
    install_bootloader,
):
    getconf.bake_all_(_sudo=True, _run=True)
    copy_partial = partial(
        rsync,
        f"{ctx.obj.resources}/",
        a=True,
        v={"repeat": 2},
        c=True,
        z={"repeat": 2},
    )
    if rebuild:
        if copy:
            copy_partial("/etc/nixos/")
        nixos_rebuild(rebuild, *ctx.args, show_trace=True)
    else:
        if copy or all:
            update_datasets(ctx)
            copy_partial("/mnt/etc/nixos/")
        if generate or all:
            nixos_generate_config(root="/mnt")
        if replace or all:
            sd(
                "./hardware-configuration.nix",
                "(import ./.).nixosConfigurations.${pkgs.stdenv.targetPlatform.system}.mini-"
                + ctx.obj.host,
                "/mnt/etc/nixos/configuration.nix",
            )
            sd(
                "'device = \"\"'",
                "'device = \"!\"'",
                "/mnt/etc/nixos/hardware-configuration.nix",
            )
        if install or all:
            options = [
                # Adapted From: https://github.com/NixOS/nix/issues/2293#issuecomment-405339738
                # Documented Here: https://nixos.org/manual/nix/stable/expressions/builtins.html#:~:text=The%20fetched%20tarball%20is%20cached%20for%20a%20certain%20amount%20of%20time%20(1%20hour%20by%20default)%20in%20~/.cache/nix/tarballs/.%20You%20can%20change%20the%20cache%20timeout%20either%20on%20the%20command%20line%20with%20%2D%2Dtarball%2Dttl%20number%2Dof%2Dseconds%20or%20in%20the%20Nix%20configuration%20file%20by%20adding%20the%20line%20tarball%2Dttl%20%3D%20number%2Dof%2Dseconds.
                # The fetched tarball is cached for a certain amount of time (1 hour by default) in ~/.cache/nix/tarballs/.
                # You can change the cache timeout either on the command line with --tarball-ttl number-of-seconds
                # or in the Nix configuration file by adding the line tarball-ttl = number-of-seconds.
                # Because I'm using the flakes nixosConfigurations output, I don't need this anymore:
                # "tarball-ttl 0",
                # Adapted From: https://github.com/NixOS/nix/issues/807#issuecomment-209895935
                "build-fallback true",
            ]
            nixos_install(
                *ctx.args,
                # Because I'm using the flakes nixosConfigurations output, I don't need this anymore:
                # :I (with [f (.open (+ ctx.obj.resources "/flake.lock"))]
                # #[f[nixpkgs=https://github.com/nixos/nixpkgs/archive/{(get (.load json f) "nodes" "22-11" "original" "ref")}.tar.gz]f])
                # I=f"""nixpkgs={nix.eval(impure=True, expr='(import ./etc/nixos).inputs.nixpkgs.outPath', _run=False).strip('"')}""",
                _run=True,
                show_trace=True,
                install_bootloader=install_bootloader,
                option={"repeat-with-values": options},
            )


@strapper.command(no_args_is_help=True)
@click.option("-B", "--boot-device", type=(str, int))
@click.option("-c", "--copies", type=int, default=1)
@click.option("-d", "--deduplicated", is_flag=True)
@click.option("-e", "--encrypted", is_flag=True)
@click.option(
    "-M",
    "--host-mountpoint",
    help="Use the hostname as the mountpoint",
    is_flag=True,
    cls=oreo.Option,
    xor=["mountpoint"],
)
@click.option("-m", "--mountpoint", cls=oreo.Option, xor=["host-mountpoint"])
@click.option("-o", "--pool-options", multiple=True)
@click.option("-O", "--dataset-options", multiple=True)
@click.option(
    "-P",
    "--partition",
    multiple=True,
    cls=oreo.Option,
    xor=["raid"],
    help="Set up an entire disk; a single `-P' sets up the boot partition with the size as the value passed in (with the unit, such as `2G' for 2 gibibytes),\na second `-P' sets up the swap space similarly, and subsequent invocations sets up further unformatted partitions.\nThe final partition will be the ZFS partition, and does not need to be specified.",
)
@click.option("-p", "--pool-only", is_flag=True)
@click.option("-r", "--raid", cls=oreo.Option, xor=["partition"])
@click.option("-S", "--swap-device", type=(str, int))
@click.option("-s", "--swap", type=int, default=0)
@click.option("-z", "--zfs-devices", required=True, multiple=True)
@click.pass_context
def create(
    ctx,
    boot_device,
    copies,
    deduplicated,
    encrypted,
    host_mountpoint,
    mountpoint,
    dataset_options,
    pool_options,
    partition,
    pool_only,
    raid,
    swap_device,
    swap,
    zfs_devices,
):
    try:
        if (
            Prompt.ask(
                "THIS WILL DELETE ALL DATA ON THE SELECTED DEVICE / PARTITION! TO CONTINUE, TYPE IN 'ZFS CREATE'!\n\t"
            )
            == "ZFS CREATE"
        ):
            dataset_options_dict = Dict(
                xattr="sa",
                acltype="posixacl",
                mountpoint=("/" + ctx.obj.host)
                if host_mountpoint
                else (mountpoint or "none"),
                compression="zstd-19",
                checksum="edonr",
                atime="off",
                relatime="off",
                copies=copies,
            )
            pool_options_dict = Dict(autotrim="on", altroot="/mnt", autoexpand="on")
            command = partial(zpool.create, f=True, _run=True)
            no_raid_error_message = "Sorry! For multiple zfs devices a raid configuration must be provided using `-r / --raid'!"
            if len(zfs_devices) == 1:
                if raid:
                    raise click.UsageError(no_raid_error_message)
                else:
                    zfs_device = zfs_devices[0]
            else:
                if raid:
                    zfs_device = f"{raid} {' '.join(zfs_devices)}"
                else:
                    raise click.UsageError(no_raid_error_message)
            if partition or boot_device:
                parted.bake_("--", _sudo=True, s=True, a="optimal")
            if partition:
                zfs_name = ctx.obj.host
                parted(zfs_device, "mklabel", "gpt")
                for [i, p] in enumerate(partition):
                    parted(
                        zfs_device,
                        "mkpart",
                        "primary",
                        partition[i - 1] if i else "0%",
                        p,
                    )
                parted(
                    zfs_device,
                    "mkpart",
                    "primary",
                    partition[-1],
                    "100%",
                )
                parted(
                    zfs_device,
                    "name",
                    3 if len(partition) > 1 else 2,
                    zfs_name,
                )
            if partition or boot_device:
                if boot_device:
                    device = boot_device[0]
                    index = boot_device[1]
                    parted(device, "mkfs", index, "fat32")
                    parted(device, "set", index, "boot", "on")
                    parted(device, "set", index, "esp", "on")
                else:
                    parted(zfs_device, "name", 1, ctx.obj.host + "-boot")
                    parted(zfs_device, "mkfs", 1, "fat32")
                    parted(zfs_device, "set", 1, "boot", "on")
                    parted(zfs_device, "set", 1, "esp", "on")
            if len(partition) > 1 or swap_device:
                if swap_device:
                    parted(swap_device[0], "mkfs", swap_device[1], "linux-swap")
                else:
                    parted(zfs_device, "name", 2, ctx.obj.host + "-swap")
                    parted(zfs_device, "mkfs", 2, "linux-swap")
            for dataset in zfs.list(r=True, H=True, _list=True, _split=True):
                if ctx.obj.host in dataset:
                    zpool.export(ctx.obj.host, f=True, _ignore_stderr=True)
            if encrypted:
                dataset_options_dict.encryption = "aes-256-gcm"
                dataset_options_dict.keyformat = "passphrase"
            if deduplicated:
                dataset_options_dict.dedup = "edonr,verify"
            if os.path.ismount("/mnt"):
                umount("/mnt", R=True)
            zpool.export(ctx.obj.host, f=True, _ignore_stderr=True)
            dataset_options_dict.update(
                {kv[0]: kv[1] for item in pool_options for kv in (item.split("="),)}
            )
            pool_options_dict.update(
                {kv[0]: kv[1] for item in dataset_options for kv in (item.split("="),)}
            )
            command(
                ctx.obj.host,
                "/dev/disk/by-label/" + zfs_name if partition else zfs_device,
                O={
                    "repeat-with-values": (
                        f"{k}={v}" for [k, v] in dataset_options_dict.items()
                    )
                },
                o={
                    "repeat-with-values": (
                        f"{k}={v}" for [k, v] in pool_options_dict.items()
                    )
                },
            )
            update_datasets(
                ctx,
                swap=swap,
                encrypted=encrypted,
                deduplicated=deduplicated,
                pool=True,
                reserved_only=pool_only,
            )
        else:
            print("Sorry; not continuing!\n\n")
    finally:
        zpool.export(ctx.obj.host, f=True, _ignore_stderr=True)


@strapper.command(no_args_is_help=True, name="mount")
@click.option("-b", "--boot-device")
@click.option("-d", "--deduplicated", is_flag=True)
@click.option("-e", "--encrypted", is_flag=True)
@click.option("-r", "--root-device")
@click.option("-s", "--swap", cls=oreo.Option, xor=["swap-device"], is_flag=True)
@click.option("-S", "--swap-device", cls=oreo.Option, xor=["swap"])
@click.option("-i", "--install", is_flag=True)
@click.option("-I", "--install-bootloader", is_flag=True)
@click.pass_context
def _mount(
    ctx,
    boot_device,
    deduplicated,
    encrypted,
    root_device,
    swap,
    swap_device,
    install,
    install_bootloader,
):
    update_datasets(
        ctx,
        root_device=root_device,
        encrypted=encrypted,
        deduplicated=deduplicated,
        swap=swap,
    )

    for dataset in zfs.list(r=True, H=True, _list=True, _split=True):
        if ctx.obj.host in dataset:
            break
    else:
        zpool(ctx.obj.host, _subcommand="import", f=True)

    if encrypted:
        zfs.load_key(ctx.obj.host)

    try:
        Path("/mnt").mkdir()
    except FileExistsError:
        if os.path.ismount("/mnt"):
            umount("/mnt", R=True)
    if root_device:
        mount(root_device, "/mnt")
    else:
        mount(ctx.obj.host + "/system/root", "/mnt", t="zfs")

    # Adapted From: https://github.com/NixOS/nixpkgs/issues/73404#issuecomment-1011485428
    try:
        Path("/mnt/mnt").mkdir()
    except FileExistsError:
        if os.path.ismount("/mnt/mnt"):
            umount("/mnt/mnt", R=True)
    mount("/mnt", "/mnt/mnt", bind=True)

    Path("/mnt/etc/nixos").mkdir(parents=True, exist_ok=True)

    Path("/mnt/nix").mkdir(parents=True, exist_ok=True)
    mount(ctx.obj.host + "/system/nix", "/mnt/nix", t="zfs")

    Path("/mnt/persist").mkdir(parents=True, exist_ok=True)
    mount(ctx.obj.host + "/system/persist", "/mnt/persist", t="zfs")

    if boot_device:
        Path(boot := "/mnt/boot/efi").mkdir(parents=True, exist_ok=True)
        mount(boot_device, boot)
    if swap:
        swapon(
            "/dev/zvol/" + ctx.obj.host + "/swap" + hy.models.Keyword("m/run") + True
        )
    if swap_device:
        swapon(swap_device, _run=True)

    Path("/tmp").mkdir(parents=True, exist_ok=True)
    mount(ctx.obj.host + "/system/tmp", "/tmp", t="zfs", _run=True)

    Path("/tmp/nix").mkdir(parents=True, exist_ok=True)
    mount(
        ctx.obj.host + "/system/tmp/nix",
        "/tmp/nix",
        t="zfs",
        _run=True,
    )

    if install or install_bootloader:
        ctx.invoke(main, all=True, install_bootloader=install_bootloader)


@strapper.command()
@click.option("-d", "--deduplicated", is_flag=True)
@click.option("-e", "--encrypted", is_flag=True)
@click.option(
    "-f",
    "--files",
    is_flag=True,
    help="Update datasets.nix with any new datasets; the default",
)
@click.option(
    "-p",
    "--pool",
    is_flag=True,
    help="Update the pool and datasets.nix with any new datasets",
)
@click.option("-r", "--root-device")
@click.option("-s", "--swap", type=int, default=0)
@click.pass_context
def update(ctx, deduplicated, encrypted, files, pool, root_device, swap):
    try:
        ud = partial(
            update_datasets,
            ctx,
            swap=swap,
            encrypted=encrypted,
            deduplicated=deduplicated,
            root_device=root_device,
        )
        if files:
            ud()
        elif pool:
            ud(pool=True)
        else:
            ud()
    finally:
        zpool.export(ctx.obj.host, f=True, _ignore_stderr=True)
