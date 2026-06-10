## Installation

```console
$ unset SPACK_PYTHON  # ensure Spack uses the Python from the virtual environment
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install "git+https://github.com/USERNAME/spack-solve-visualize#egg=spack_solve_visualize"
```

## Usage

To visualize intermediate models during spec solving:

```console
$ spack solve-visualize zlib
```

This displays all intermediate models generated during the solve, showing the optimization cost and dependency tree for each model.

To save the output to a file:

```console
$ spack solve-visualize "hdf5+mpi" -o models.txt
```

To perform a fresh solve without reusing installed specs:

```console
$ spack solve-visualize cmake --fresh
```
