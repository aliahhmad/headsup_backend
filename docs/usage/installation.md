# How to Use this project

!!! info "Contribute!"
    If you make changes to the existing code, why not create a
    feature-branch and a Pull-Request to have it included into the Template? 😊

    See [Contributing](../contributing.md) for more info.

## Installation

### As a GitHub template

Click the 'Use this template' button at the top of the Repository on GitHub.
This will create a new repository in your personal GitHub account (Not a Fork)
which you can then Clone and start working on.

It is assumed that you have at least some knowledge of
[FastAPI](https://fastapi.tiangolo.com/){:target="_blank"} to use this template,
there are very good
[Basic](https://fastapi.tiangolo.com/tutorial/){:target="_blank"} and
[Advanced](https://fastapi.tiangolo.com/advanced/){:target="_blank"} User Guides
on the FastAPI website .

### As a Fork

You can also fork this project as usual, which allows you to easily bring it up
to date with changes and improvements as they happen. This does have the
posibility of conflicts. As long as you stick to adding new models and routes
but not modifying the existing base code it should be ok.

### Upgrading from a Patch file

In the
[CHANGELOG.md](https://github.com/seapagan/fastapi-template/blob/main/CHANGELOG.md){:target="_blank"}
file, there are links to `Patch` and `Diff` files to enable you to upgrade from
a previous release. These can be applied by Git and will upgrade the Template
code to that release. The same warning applies as above - your changes can cause
merge conflicts.

If you were a few releases behind, you can apply the patches in ascending
version order to get to the latest release.

See [git apply](https://git-scm.com/docs/git-apply){:target="_blank"} and [git
am](https://git-scm.com/docs/git-am){:target="_blank"} for more information on
how to apply these.

## Create a Virtual Environment and Install Dependencies

The project uses [uv](https://docs.astral.sh/uv/){:target="_blank"} for managing
the virtual environment and dependencies. This is a faster alternative to
`Poetry` which was used previously.

To create the virtual environment and install the dependencies, run the
following command:

```console
uv sync
```

This will create a virtual environment in the `.venv` directory and install all
the dependencies from the `pyproject.toml` file.

You can then activate the virtual environment with:

```console
source .venv/bin/activate
```

Or on Windows:

```console
.venv\Scripts\activate
```

## GitHub Actions

The API's GitHub workflows run linting, type checking, dependency review, and
security checks. Python tests and test coverage uploads are not run in GitHub
Actions, so a Codacy token is not required for these workflows.

Run the test suite locally using the
[local development instructions](../development/local.md).
