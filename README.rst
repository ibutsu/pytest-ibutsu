Ibutsu pytest plugin
====================

.. image:: https://github.com/ibutsu/pytest-ibutsu/workflows/pytest-ibutsu%20tests/badge.svg
    :target: https://github.com/ibutsu/pytest-ibutsu/actions
    :alt: CI Status

.. image:: https://results.pre-commit.ci/badge/github/ibutsu/pytest-ibutsu/main.svg
   :target: https://results.pre-commit.ci/latest/github/ibutsu/pytest-ibutsu/main
   :alt: pre-commit.ci status

.. image:: https://codecov.io/gh/ibutsu/pytest-ibutsu/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/ibutsu/pytest-ibutsu
    :alt: Coverage Status

.. image:: https://img.shields.io/pypi/v/pytest-ibutsu.svg
    :target: https://pypi.org/project/pytest-ibutsu/
    :alt: PyPI Version

.. image:: https://img.shields.io/pypi/pyversions/pytest-ibutsu.svg
    :target: https://pypi.org/project/pytest-ibutsu/
    :alt: Python Versions

This is a plugin that will report test rests from pytest to an
`Ibutsu server <https://github.com/ibutsu/ibutsu-server>`_.

Requirements
------------

- Python 3.11+
- pytest
- attrs

Installation
------------

Install the plugin via ``pip``::

    pip install pytest-ibutsu

If you're developing this plugin, you can create an editable installation::

    pip install -e .

Getting started
---------------

To push your results to the Ibutsu server, use the ``--ibutsu`` option with the URL to your server::

    pytest --ibutsu http://ibutsu-api.example.com/

Authentication
--------------

To authenticate against your Ibutsu server, use the ``--ibutsu-token`` option with a token from your
Ibutsu server. Go to your profile page, select tokens, and generate a token there. Copy and paste
the JWT token generated into this option::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-token eyJhbGci.......CA1opEQ

More options
------------

To set the source for the test results, use the ``--ibutsu-source`` option::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-source my-test-run

If you want to add metadata to each result's metadata, you can use the ``--ibutsu-data`` option::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-data key=value

You can specify multiple metadata items with spaces or with multiple ``--ibutsu-data`` options::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-data key1=value1 key2=value2

You can also specify sub-keys via dotted notation::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-data key.subkey.susbsub=value

If you need to accumulate data from multiple ``pytest`` sessions, you should provide the same UUID
into ``ibutsu-run-id`` option::

    pytest --ibutsu-run-id=<UUID string>

    pytest --ibutsu-run-id=<the same UUID string>

The archive will be rebuilt and the data on the Ibutsu server will be updated.

Set project
-----------

If your Ibutsu server requires a project set, you can do that with the ``--ibutsu-project`` option::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-project 5eb1aff37c274bcd20002476

You can also use the project ``name`` field::

    pytest --ibutsu http://ibutsu-api.example.com/ --ibutsu-project my-project

Environment Variables
----------------------

All ``--ibutsu`` options can be configured using environment variables. The plugin follows a consistent precedence order: CLI options > Environment variables > INI file settings > Defaults.

The following environment variables are supported:

- ``IBUTSU_MODE``: Set the Ibutsu mode (equivalent to ``--ibutsu``)::

    export IBUTSU_MODE=archive
    # or
    export IBUTSU_MODE=s3
    # or
    export IBUTSU_MODE=https://ibutsu-api.example.com

- ``IBUTSU_TOKEN``: Set the JWT authentication token (equivalent to ``--ibutsu-token``)::

    export IBUTSU_TOKEN=eyJhbGci<.......>CA1opEQ

- ``IBUTSU_SOURCE``: Set the test source (equivalent to ``--ibutsu-source``)::

    export IBUTSU_SOURCE=my-test-run

- ``IBUTSU_PROJECT``: Set the project ID or name (equivalent to ``--ibutsu-project``)::

    export IBUTSU_PROJECT=my-project

- ``IBUTSU_RUN_ID``: Set the test run ID (equivalent to ``--ibutsu-run-id``)::

    export IBUTSU_RUN_ID=550e8400-e29b-41d4-a716-446655440000

- ``IBUTSU_DATA``: Set extra metadata (equivalent to ``--ibutsu-data``)::

    export IBUTSU_DATA="key1=value1 key2=value2"
    # supports dotted notation
    export IBUTSU_DATA="key.subkey=value"

- ``IBUTSU_NO_ARCHIVE``: Disable archive creation (equivalent to ``--ibutsu-no-archive``)::

    export IBUTSU_NO_ARCHIVE=true

Using environment variables is particularly useful in CI/CD environments where you can set these values once and have them apply to all pytest runs.

Offline usage
-------------

If you want to still store your results, but can't upload immediately, the Ibutsu plugin can create
an archive which you can upload later. Use ``archive`` with the ``--ibutsu`` option::

    pytest --ibutsu archive

The Ibutsu plugin will save the archive in your current directory, and will print out the location.

S3 Upload
---------

If you want to upload your test artifacts to an Amazon S3 bucket, you can use the ``s3`` mode::

    pytest --ibutsu s3

This will create an archive file and upload any archive files found in the current directory to your configured S3 bucket.
It will avoid uploading the same file twice, or overwriting a potential UUID collision already in the bucket.

**Requirements for S3 upload:**

1. Configure AWS credentials using one of these methods:

   - Environment variables::

       export AWS_ACCESS_KEY_ID=your_access_key
       export AWS_SECRET_ACCESS_KEY=your_secret_key
       export AWS_REGION=your_region
       export AWS_BUCKET=your_bucket_name

   - `AWS credentials file <https://docs.aws.amazon.com/cli/v1/userguide/cli-configure-files.html>`_
   - EC2 instance profile
   - AWS IAM role

**Three Operation Modes:**

- **Archive mode**: Create local archive only::

    pytest --ibutsu archive

- **S3 mode**: Create archive and upload to S3::

    pytest --ibutsu s3

- **Server mode**: Send directly to Ibutsu API endpoint::

    pytest --ibutsu https://ibutsu-api.example.com

  Note: In server mode, archives are created by default unless ``--ibutsu-no-archive`` is specified.

Usage
-----

With this plugin installed, and the configuration set up, your test results will automatically be
sent to the Ibutsu server.


Hooks
-----

The plugin has its own hooks. They are defined in ``newhooks.py``.

Development
-----------

To set up for development, clone the repository and install in development mode::

    git clone https://github.com/ibutsu/pytest-ibutsu.git
    cd pytest-ibutsu
    uv sync --group dev

Running Tests with Coverage
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The project uses pytest with coverage reporting. To run the full test suite::

    uv run pytest

This will automatically generate:

- Terminal coverage report
- HTML coverage report in ``htmlcov/``
- XML coverage report as ``coverage.xml``

Coverage configuration is in ``pyproject.toml`` under ``[tool.coverage.*]`` sections.

The minimum coverage threshold is set to 74%. Tests will fail if coverage falls below this threshold.

To run tests without coverage (faster for development)::

    uv run pytest --no-cov

To view the HTML coverage report::

    open htmlcov/index.html  # macOS
    xdg-open htmlcov/index.html  # Linux
