if [ -d /opt/qcom-adreno/lib ]; then
  export LD_LIBRARY_PATH="/opt/qcom-adreno/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

export RUSTICL_ENABLE=freedreno
export DISABLE_FAST_IDIV=1
export EMULATED_DTYPES=long
