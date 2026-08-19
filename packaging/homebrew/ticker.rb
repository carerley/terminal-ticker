class Ticker < Formula
  include Language::Python::Virtualenv

  desc "Quick no-key stock quotes for the terminal"
  homepage "https://github.com/carerley/terminal-ticker"
  url "https://github.com/carerley/terminal-ticker/archive/refs/tags/v0.4.0.tar.gz"
  sha256 "0f8bdc446ae11200a8ed3329ad7c0484dda649494c2d7adc57ceb9e5464bc871"
  license "MIT"

  depends_on "python"

  resource "websockets" do
    url "https://files.pythonhosted.org/packages/21/e6/26d09fab466b7ca9c7737474c52be4f76a40301b08362eb2dbc19dcc16c1/websockets-15.0.1.tar.gz"
    sha256 "82544de02076bafba038ce055ee6412d68da13ab47f0c60cab827346de828dee"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "ticker 0.4.0", shell_output("#{bin}/ticker --version")
  end
end
