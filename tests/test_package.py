def test_package_exposes_version():
    import lintarr

    assert isinstance(lintarr.__version__, str)
    assert lintarr.__version__
