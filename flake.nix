{
  nixConfig = {
    # Adapted From: https://github.com/divnix/digga/blob/main/examples/devos/flake.nix#L4
    accept-flake-config = true;
    auto-optimise-store = true;
    builders-use-substitutes = true;
    cores = 0;
    extra-experimental-features =
      "nix-command flakes impure-derivations recursive-nix";
    fallback = true;
    flake-registry =
      "https://raw.githubusercontent.com/syvlorg/flake-registry/master/flake-registry.json";
    keep-derivations = true;
    keep-outputs = true;
    max-free = 1073741824;
    min-free = 262144000;
    show-trace = true;
    trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
      "nickel.cachix.org-1:ABoCOGpTJbAum7U6c+04VbjvLxG9f0gJP5kYihRRdQs="
      "sylvorg.cachix.org-1:xd1jb7cDkzX+D+Wqt6TemzkJH9u9esXEFu1yaR9p8H8="
    ];
    trusted-substituters = [
      "https://cache.nixos.org/"
      "https://nix-community.cachix.org"
      "https://nickel.cachix.org"
      "https://sylvorg.cachix.org"
    ];
    warn-dirty = false;
  };
  description = "A python application to help you install NixOS on a ZFS root!";
  inputs = rec {
    bundle = {
      url = "https://github.com/sylvorg/bundle.git";
      type = "git";
      submodules = true;
    };
    valiant.follows = "bundle/valiant";
    nixpkgs.follows = "bundle/nixpkgs";

    pyPkg-bakery.url =
      "git+https://github.com/syvlorg/bakery.git";

    flake-utils.url = "github:numtide/flake-utils";
    flake-compat = {
      url = "github:edolstra/flake-compat";
      flake = false;
    };
  };
  outputs = inputs@{ self, flake-utils, ... }:
    with builtins;
    with inputs.bundle.lib;
    with flake-utils.lib;
    inputs.bundle.mkOutputs.python rec {
      inherit inputs self;
      type = "hy";
      pname = "strapper";
      callPackage =
        args@{ callPackage, util-linux, getconf, parted, sd, rsync }:
        callPackage (iron.mkPythonPackage {
          inherit self inputs;
          recursiveOverrides = toList "postCheck";
          package = rec {
            owner = "syvlorg";
            src = ./.;
            propagatedBuildInputs = [ util-linux getconf parted sd rsync ];
            postPatch = ''
              substituteInPlace pyproject.toml \
                --replace "bakery = { git = \"https://github.com/syvlorg/bakery.git\", branch = \"main\" }" ""
              substituteInPlace setup.py \
                --replace "'bakery @ git+https://github.com/syvlorg/bakery.git@main'" "" || :
            '';
            meta.description =
              "A python application to help you install NixOS on a ZFS root!";
          };
        }) { };
    } { isApp = true; };
}
