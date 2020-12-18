#!/usr/bin/env bash

timereqsproxy() {
	cat urls.txt | while read line; do
		echo "$line"
		curl \
			-x localhost:9060 \
	        --output /dev/null \
	        --silent \
	        --write-out '%{time_total}\n' \
	        $line
	done
}

timereqsnorm() {
	cat urls.txt | while read line; do
		echo "$line"
		curl \
	        --output /dev/null \
	        --silent \
	        --write-out '%{time_total}\n' \
	        $line
	done
}

echo "All URL times without the proxy:"
timereqsnorm
echo "All URL times without caching:"
timereqsproxy 
echo "All URL times with caching:"
timereqsproxy