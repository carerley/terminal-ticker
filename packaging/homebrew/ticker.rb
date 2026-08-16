class Ticker < Formula
  desc "Quick no-key stock quotes for the terminal"
  homepage "https://github.com/carerley/terminal-ticker"
  url "https://github.com/carerley/terminal-ticker/archive/refs/tags/v0.3.0.tar.gz"
  sha256 "e22ba05323c1d7b22de046d1beb68f993939ee28ace75cf84c97f7e10fdf2643"
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
    assert_match "ticker 0.3.0", shell_output("#{bin}/ticker --version")
  end
end
