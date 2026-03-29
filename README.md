# S-Bench Setup Guide

This project is hosted on a machine inside my local network and is accessed through Tailscale.

## Prerequisites

Before using the tool, make sure you have:

- A device with [Tailscale](https://tailscale.com/) installed
- Access to the Tailscale shared invite link
- Network access to the host machine running the server

## How to Connect

### 1. Join the Tailscale network
Click on the shared Tailscale invite link and accept the invitation to join the network.

Once you have joined successfully, your device will be connected to the same private network as the host machine.

### 2. Start the server on the host machine
On the host machine, launch the server so that it is running on port `8765`.

### 3. Open the application in your browser
After joining the Tailscale network, open the annotations interface and change the server URL to:

```bash
http://<IP_ADDRESS>:8765