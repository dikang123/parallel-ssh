#!/bin/bash -e

brew install pyenv || brew outdated pyenv || brew upgrade pyenv

export PYENV_VERSION=$PYENV
if [[ ! -d "$HOME/.pyenv/versions/$PYENV_VERSION" ]]; then
    pyenv install $PYENV_VERSION
fi
export PYENV_ROOT="$HOME/.pyenv"
pyenv global $PYENV_VERSION
pyenv versions
eval "$(pyenv init -)"
which python
python -m pip install -U virtualenv
virtualenv -p "$(which python)" venv
source venv/bin/activate
python -V
python -m pip install -U setuptools pip
pip install -U delocate wheel
pip install -r requirements.txt
pip wheel --no-deps .
cp /usr/local/lib/libssh2* .
delocate-listdeps --all *.whl
delocate-wheel -v *.whl
delocate-listdeps --all *.whl
ls -l *.whl
rm -f *.dylib
pip install -v *.whl
pwd; mkdir -p temp; cd temp; pwd
python -c "import pssh.clients"
cd ..; pwd
deactivate

mv -f *.whl wheels/
