import math

import numpy as np
import pytest

from report_generator.core.models import CancelledError
from report_generator.core.touchstone import interpolate_impedance, read_touchstone


def write_network(tmp_path, matrices, frequencies=(1, 3), representation="RI", reference=2):
    n = matrices[0].shape[0]
    path = tmp_path / f"synthetic.s{n}p"
    rows = [f"# MHz S {representation} R {reference}", "! Port19_LOAD::RAIL/0", "! Port23_SOURCE::RAIL/0",
            "! Port[1] = LOAD-RAIL/0", "! Port[2] = SOURCE-RAIL/0"] if n == 2 else [f"# MHz S {representation} R {reference}"]
    for frequency, matrix in zip(frequencies, matrices):
        flat = matrix.T.flat if n == 2 else matrix.flat
        pairs = []
        for z in flat:
            if representation == "RI":
                pairs.extend([z.real, z.imag])
            else:
                magnitude = abs(z)
                pairs.extend([20 * math.log10(magnitude) if representation == "DB" else magnitude, np.angle(z, deg=True)])
        values = [frequency, *pairs]
        # A record deliberately continues across lines and contains inline comments.
        rows.extend([" ".join(map(str, values[:5])) + " ! sample", " ".join(map(str, values[5:]))])
    path.write_text("\n".join(rows), encoding="ascii")
    return path


@pytest.mark.parametrize("representation", ["RI", "MA", "DB"])
def test_full_matrix_conversion_preserves_header_and_complex_interpolation(tmp_path, representation):
    matrices = [np.array([[.1 + .02j, .2 + .01j], [.15 - .01j, .3 + .02j]]),
                np.array([[.2 + .2j, .1 + .02j], [.3 + .01j, .25 - .1j]])]
    data = read_touchstone(write_network(tmp_path, matrices, representation=representation))
    expected = np.asarray([np.diag(2 * np.linalg.solve(np.eye(2) - s, np.eye(2) + s)) for s in matrices])
    np.testing.assert_allclose(data.z_diagonal, expected)
    np.testing.assert_allclose(data.frequency_hz, [1e6, 3e6])
    assert data.reference_ohm == 2
    assert data.ports[0].index == 1
    assert data.ports[0].name == "LOAD-RAIL/0"
    assert data.ports[0].description == "Port19_LOAD::RAIL/0"
    assert interpolate_impedance(data, 1, 1e6) == (abs(expected[0, 0]), False)
    value, interpolated = interpolate_impedance(data, 1, 2e6)
    assert value == pytest.approx(abs((expected[0, 0] + expected[1, 0]) / 2))
    assert interpolated
    assert interpolate_impedance(data, 1, 0) == (None, False)
    assert interpolate_impedance(data, 1, 4e6) == (None, False)
    assert not np.isclose(expected[0, 0], 2 * (1 + matrices[0][0, 0]) / (1 - matrices[0][0, 0]))


def test_singular_record_stays_missing_and_blocks_interpolation(tmp_path):
    path = write_network(tmp_path, [np.array([[0j]]), np.array([[1 + 0j]]), np.array([[.5 + 0j]])], (1, 2, 3))
    data = read_touchstone(path)
    assert np.isnan(data.z_diagonal[1, 0])
    assert len(data.warnings) == 1
    assert interpolate_impedance(data, 1, 2e6) == (None, False)
    assert interpolate_impedance(data, 1, 2.5e6) == (None, False)


@pytest.mark.parametrize("content", [
    "1 0 0", "# Hz S RI R 0\n1 0 0", "# Hz S RI R nan\n1 0 0",
    "[Version] 2.0\n# Hz S RI R 1\n1 0 0", "# Hz Y RI R 1\n1 0 0",
    "# Hz S RI R 1\n1 0", "# Hz S RI R 1\n1 0 0\n1 0 0",
    "# Hz S RI R 1\n2 0 0\n1 0 0", "# Hz S RI R 1\n1 inf 0",
    "# Hz S RI R 1\n1 0 0garbage", "# Hz S RI R 1\n1 0 0 2",
    "# Hz S RI R 1\n! Port[2] = RAIL/0\n1 0 0", "# Hz S MA R 1\n1 -1 0",
])
def test_rejects_malformed_input(tmp_path, content):
    path = tmp_path / "invalid.s1p"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_touchstone(path)


def test_cancellation_and_changed_input(tmp_path):
    path = write_network(tmp_path, [np.array([[0j]])], (1,))
    with pytest.raises(CancelledError):
        read_touchstone(path, cancel=lambda: True)

    def change_source(*_):
        path.write_text(path.read_text() + "\n! changed\n")

    with pytest.raises(ValueError, match="변경"):
        read_touchstone(path, progress=change_source)


def test_three_port_row_order_and_default_options(tmp_path):
    matrix = np.array([[.1, .2, .05], [.3, .2, .04], [.01, .06, .1]], dtype=complex)
    path = write_network(tmp_path, [matrix], (1,))
    data = read_touchstone(path)
    np.testing.assert_allclose(data.z_diagonal[0], np.diag(2 * np.linalg.solve(np.eye(3) - matrix, np.eye(3) + matrix)))
    default = tmp_path / "default.s1p"
    default.write_text("#\n1 0.5 0\n")
    data = read_touchstone(default)
    assert data.frequency_hz[0] == 1e9
    assert data.z_diagonal[0, 0] == pytest.approx(150)


def test_record_memory_limits_reject_oversized_inputs(tmp_path, monkeypatch):
    excessive_ports = tmp_path / "invalid.s1025p"
    excessive_ports.write_text("#\n1 0 0\n")
    with pytest.raises(ValueError, match="1024"):
        read_touchstone(excessive_ports)
    import report_generator.core.touchstone as parser
    monkeypatch.setattr(parser, "_MAX_RECORD_BYTES", 32)
    path = tmp_path / "invalid.s1p"
    path.write_text("# Hz S RI R 1\n" + " " * 33 + "\n")
    with pytest.raises(ValueError, match="64MiB"):
        read_touchstone(path)
    path = tmp_path / "invalid.s3p"
    path.write_text("# Hz S RI R 1\n1 " + "0 " * 8 + "\n" + "0 " * 10 + "\n")
    with pytest.raises(ValueError, match="64MiB"):
        read_touchstone(path)
