# -*- coding: utf-8 -*-
import pytest


def test_help_message(testdir):
    result = testdir.runpytest("--help")
    # fnmatch_lines does an assertion internally
    result.stdout.fnmatch_lines(["ibutsu:", "*--ibutsu=URL*URL for the Ibutsu server"])


@pytest.mark.skip("Skipped because I'm not a fan of this test.")
def test_mark_skip():
    pass


@pytest.mark.skipif(True, reason="Skipping due to True equaling True")
def test_mark_skipif():
    pass


def test_skip():
    pytest.skip("I really don't like this test, but I had to think about it first.")


def test_pass():
    pass


def test_fail():
    pytest.fail("I don't like tests that pass")
