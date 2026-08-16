class Ticker < Formula
  desc "Quick no-key stock quotes for the terminal"
  homepage "https://github.com/carerley/terminal-ticker"
  url "https://github.com/carerley/terminal-ticker/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "c73bc7cd35f896520af33052cdff793a6ddf70db64ec9115423efcf45e98afd9"
  license "MIT"

  depends_on "python"

  def install
    libexec.install "src/ticker"

    launcher = libexec/"ticker-cli"
    launcher.write <<~PYTHON
      #!#{formula_opt_bin("python")}/python3
      from ticker.cli import main
      raise SystemExit(main())
    PYTHON
    launcher.chmod 0755
    bin.install_symlink launcher => "ticker"
  end

  test do
    assert_match "ticker 0.2.0", shell_output("#{bin}/ticker --version")
  end
end
