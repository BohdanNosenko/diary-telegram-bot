{
  description = "vlog-journal development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            uv
            ffmpeg-full
            rclone
            p7zip
            ollama
            jq
            git
          ];

          shellHook = ''
            echo "🚀 vlog-journal dev environment loaded!"
            echo "Run 'uv sync' to install Python dependencies."
          '';
        };
      }
    );
}
