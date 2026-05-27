export EDITOR='vim'
export VIMINIT='source $MYVIMRC'
export MYVIMRC="~/.vimrc"

[ -f $HOME/.profile ] && source $HOME/.profile
[ -f $HOME/.bashrc ] && source $HOME/.bashrc

if [ -d "/data/openpilot" ] ; then
  cd /data/openpilot
fi
