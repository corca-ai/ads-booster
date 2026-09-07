ARG UBUNTU_VERSION=22.04
FROM ubuntu:${UBUNTU_VERSION}
ENV container=docker
RUN apt-get update -qq && apt-get install -y -qq systemd systemd-sysv dbus sudo && apt-get clean
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
