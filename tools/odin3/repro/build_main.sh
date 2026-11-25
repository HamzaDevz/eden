#!/bin/zsh
export JAVA_HOME=$(/usr/libexec/java_home -v 21) ANDROID_HOME=~/Library/Android/sdk
yes | ~/Library/Android/sdk/cmdline-tools/latest/bin/sdkmanager "ndk;30.0.16248370" > /dev/null 2>&1 || { echo NDK_FAIL; exit 1; }
ls ~/Library/Android/sdk/ndk/30.0.16248370/source.properties || exit 1
cd /Users/hamzatei/PhpstormProjects/eden/src/android && ./gradlew assembleGenshinSpoofRelWithDebInfo --console=plain
