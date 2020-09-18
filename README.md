# APS

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Unit Testing](https://github.com/funcwj/aps/workflows/Unit%20Testing/badge.svg)](https://github.com/funcwj/aps/workflows/Unit%20Testing/badge.svg)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)


My workspace for speech related tasks, including single/multi-channel speech enhancement & separation & recognition. The goal is to make it easy, flexible and efficient to conduct experiments, verify ideas, implement and evaluate novel neural networks. The design and usage of the package are based on my experience in the last few years.

## Introduction

* [Overivew](doc/overview.md)
* [Structure](doc/code.md)
* [Instruction](doc/instruction.md)
* [Examples](egs)

## Setup

```shell
git clone https://github.com/funcwj/aps
# set up the python environments
pip install -r requirements.txt
# set up the git hook scripts
pre-commit install
```

## Acknowledge

The author started the project (2019.03) when he was a master student in the Audio, Speech and Language Processing Group (ASLP) in Northwestern Polytechnical University (NWPU), Xi'an, China.
