{
    description = "A python application to help you install NixOS on a ZFS root!";
    inputs = rec {
        settings.url = github:sylvorg/settings;
        titan.url = github:syvlorg/titan;
        flake-utils.url = github:numtide/flake-utils;
        flake-compat = {
            url = "github:edolstra/flake-compat";
            flake = false;
        };
        py3pkg-bakery.url = github:syvlorg/bakery;
        py3pkg-pytest-hy.url = github:syvlorg/pytest-hy;
    };
    outputs = inputs@{ self, flake-utils, settings, ... }: with builtins; with settings.lib; with flake-utils.lib; settings.mkOutputs rec {
        inherit inputs;
        type = "hy";
        pname = "strapper";
        isApp = true;
        extras.appPathUseNativeBuildInputs = true;
        callPackage = args@{ stdenv
            , util-linux
            , getconf
            , parted
            , sd
            , rsync
            , bakery
            , pname
        }: j.mkPythonPackage self stdenv [ "postCheck" ] (rec {
            owner = "syvlorg";
            inherit pname;
            src = ./.;
            buildInputs = attrValues (filterAttrs (n: v: (isDerivation v) && (! (elem n [ "stdenv" ]))) args);
            propagatedBuildInputs = [ bakery ];
            postPatch = ''
                substituteInPlace pyproject.toml --replace "bakery = { git = \"https://github.com/${owner}/bakery.git\", branch = \"main\" }" ""
                substituteInPlace setup.py --replace "'bakery @ git+https://github.com/${owner}/bakery.git@main'" ""
            '';
            meta.description = "A python application to help you install NixOS on a ZFS root!";
        });
    };
}
