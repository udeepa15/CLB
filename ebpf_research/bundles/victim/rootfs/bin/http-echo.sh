#!/bin/sh
printf 'HTTP/1.1 200 OK\r\n'
printf 'Content-Type: text/plain\r\n'
printf 'Content-Length: 3\r\n'
printf 'Connection: close\r\n\r\n'
printf 'ok\n'
