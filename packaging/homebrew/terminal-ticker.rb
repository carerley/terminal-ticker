class TerminalTicker < Formula
  desc "Quick no-key stock quotes for the terminal"
  homepage "https://github.com/carerley/terminal-ticker"
  url "https://github.com/carerley/terminal-ticker/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "3bcf3bcdb78f88f390c5930d2150a172cf8f636c28695c78c78ab936cb568315"
  license "MIT"

  depends_on "python@3.14"

  def install
    libexec.install "src/ticker"

    launcher = libexec/"ticker-cli"
    launcher.write <<~PYTHON
      #!#{formula_opt_bin("python@3.14")}/python3.14
      from ticker.cli import main
      raise SystemExit(main())
    PYTHON
    launcher.chmod 0755
    bin.install_symlink launcher => "ticker"
  end

  test do
    assert_match "ticker 0.1.0", shell_output("#{bin}/ticker --version")
  end
end
