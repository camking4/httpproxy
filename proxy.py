import socket, select
import sys
import email
import ssl
import threading
# from ratelimit import limits

# Constants
MAX_CALLS = 15
MAX_PERIOD = 900
TIME_OUT = 10 # in seconds
DEFAULT_PORT = 80
BUFSIZE = 8196 # in bytes
TEMP_BUFSIZE = 1024 # in bytes
GOOD_CL_RESP = b'HTTP/1.1 200 OK\r\n\r\n'
BAD_CL_RESP = b'HTTP/1.1 500 Internal server error\r\n\r\n'
NOT_CACHED = b'Not in Cache'

####################### Round Trip Class #######################
# This class is used to easily contain all of the information we need about a
#   round trip connection
class RoundTripConn:
    def __init__(self, client_sock, client_addr, dest_sock, dest_addr, resp_client, req_dest, is_ssl=False):
        self.client_sock = client_sock
        self.client_addr = client_addr
        self.dest_sock = dest_sock
        self.dest_addr = dest_addr
        self.resp_client = resp_client
        self.req_dest = req_dest
        self.cache_key = ""
        self.is_ssl = ssl

    def add_key(self, new_key):
        self.cache_key = new_key

def clean_connections(connections, writes, broken):
    for conn in connections:
        if conn.fileno() == -1:
            print(conn)
            try:
                connections.remove(conn)
                conn.close()
            except Exception:
                "Already Removed"

    for write in writes:
        if write.fileno() == -1:
            print(write)
            try:
                writes.remove(write)
                write.close()
            except Exception:
                "Already Removed"

    for error in broken:
        if error.fileno() == -1:
            print(error)
            try:
                broken.remove(error)
                broken.close()
            except Exception:
                "Already Removed"
            

# The following functions in this section relate to a list of RoundTripConns
def get_dest_client(round_trips, dest):
    for rtconn in round_trips:
        # print("DEST:", rtconn.dest_sock.fileno(), "vs", dest.fileno())
        if (rtconn.dest_sock == dest):
            return rtconn
    return -1

def get_client_dest(round_trips, client):
    for rtconn in round_trips:
        # print("CLIENT:", rtconn.client_sock.fileno(), "vs", client.fileno())
        if (rtconn.client_sock == client):
            return rtconn
    return -1

def find_rt_item(round_trips, sock):
    rt_item = get_dest_client(round_trips, sock)
    if (rt_item == -1):
        return get_client_dest(round_trips, sock)
    return rt_item

def find_rt_error(round_trips, connections, sock):
    rt_item = find_rt_item(round_trips, sock)
    if (rt_item == -1):
        sock.close()
        connections.remove(sock)
        print("GOT BAD RT ITEM")
        return -1
    return rt_item

##################### Initialization Functions #####################
def set_up_server(port):
    HOST = ''

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.setblocking(0)
    server.bind((HOST, port))
    server.listen(10)
    print("Setup Server at IP:", HOST)

    return server

####################### Networking Functions #######################
######## READS
# @limits(calls=MAX_CALLS, period=MAX_PERIOD)
def handle_read_sock(sock, server, writes, connections, round_trips, threads, wlock, rlock, proxy_cache):
    # sock.settimeout(TIME_OUT)
    if sock == server:
        try:
            read_thread = threading.Thread(target=read_serv_sock, args=[sock, connections, writes, round_trips, wlock, rlock, proxy_cache])
            threads.append(read_thread)
            print("Starting READ thread")
            read_thread.start()
        except Exception as e:
            print("Failed to handle accept connection:", e)
            return False
        return True
    elif sock != server:
        try:
            read_thread = threading.Thread(target=read_dest, args=[sock, writes, connections, round_trips, wlock, rlock])
            # read_dest(sock, writes, connections, round_trips)
            threads.append(read_thread)
            read_thread.start()
            print("Starting READ thread")
        except socket.timeout as e:
            print("WARNING: Socket conn timed out with ERROR of", e)
            try:
                connections.remove(sock)
            except Exception:
                connections = connections
            sock.close()
            return False
        except Exception as e:
            print("Got unhandled error", e)
            return False
        return True

# @limits(calls=MAX_CALLS, period=MAX_PERIOD)
def read_serv_sock(sock, connections, writes, round_trips, wlock, rlock, proxy_cache):
    print("READING ON SERVER SOCK")
    try:
        new_client, new_clientaddr = sock.accept()
    except Exception:
        # nothing to accept
        print("FAILED TO ACCEPT")
        return

    # HANDLE AN EXISTING SSL CONNECTION
    enc_comm = find_rt_item(round_trips, sock)
    print(enc_comm)
    if(enc_comm != -1):
        if(enc_comm.is_ssl):
            if(enc_comm.client_sock == sock):
                print("HANDLING SSL CONN")
                enc_comm.req_dest = msg
                # if(enc_comm.dest_sock != -1):
                writes.append(enc_comm.dest_sock)
            else:
                enc_comm.resp_client = msg
                # if(enc_comm.client_sock != -1):
                writes.append(enc_comm.client_sock)
            return

    new_client.setblocking(1)
    # writes.append(new_conn)
    # print("Found a socket to read of value", sock.fileno())

    # print("Getting msg")
    # try:
    undecoded_msg = new_client.recv(BUFSIZE)
    # finally:
        # print("FAILED TO RECIEVE ON SERVER, CLOSING CONN")
        # connections.remove(sock)
        # new_client.close()
        # return
    try:
        msg = undecoded_msg.decode("utf-8")
        # print(msg)
    except Exception:
        print("Failed to decode a parsable message")
        return
        # Assume encrypted, pass onto the connections partner
        

    cached_resp = check_cache(proxy_cache, msg)

    if cached_resp != NOT_CACHED:
        # Write to the client immediately, then add to connections, return
        print("Response Found In Cache")
        # print(len(cached_resp))
        # print("BYTES SENT VS MSG SIZE:", new_client.send(cached_resp), "vs", len(cached_resp))
        new_client.send(cached_resp)
        #connections.append(new_client)
        return

    hostname, port = parse_hostname_port(msg)

    DEST_HOST = socket.getfqdn(socket.gethostbyname(hostname))
    DEST_PORT = port
    dest = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dest.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if msg[0] == 'C':
        try: 
            print("         MSG 0", msg[0])
            # dest.connect((DEST_HOST, DEST_PORT))
            print("SENDING", GOOD_CL_RESP)
            new_client.send(GOOD_CL_RESP)
            print("SENT")
            new_rtconn = RoundTripConn(new_client, new_clientaddr, dest, socket.gethostbyname(hostname), "", undecoded_msg, True)
            round_trips.append(new_rtconn)
            # if(new_client != -1):
            connections.append(new_client)
        except Exception as e:
            new_client.send(BAD_CL_RESP)
            print("Failed to append connection with error", e)
    else:
        dest.connect((DEST_HOST, DEST_PORT))
        new_rtconn = RoundTripConn(new_client, new_clientaddr, dest, socket.gethostbyname(hostname), "", undecoded_msg)
        new_rtconn.add_key(msg)
        round_trips.append(new_rtconn)
        with wlock:
            print("Adding writes")
            # if(dest != -1):
            writes.append(dest)

# @limits(calls=MAX_CALLS, period=MAX_PERIOD)
def read_dest(sock, writes, connections, round_trips, wlock, rlock):
    print("READING RESPONSE FROM DEST")
    sock.settimeout(TIME_OUT)
    rt_item = find_rt_error(round_trips, connections, sock)
    if (rt_item == -1):
        return;

    if sock == rt_item.client_sock:
        print("READING FROM CLIENT")

    msg = b""
    while(True):
        # print("WAITING TO RECV")
        try:         
            tempmsg = sock.recv(TEMP_BUFSIZE)
        except Exception as e:
            if(rt_item.is_ssl):
                break;
            else:
                sock.close()
                print("FAILED TO RECIEVE FROM DEST with error", e )
                with rlock:
                    connections.remove(sock)
                return
        # print("GOT MSG", tempmsg)
        if len(tempmsg) == 0:
            break;
        msg += tempmsg

    with rlock:
        print("REMOVING READ")
        connections.remove(sock)

    if (sock == rt_item.client_sock):
        with wlock:
            rt_item.req_dest = msg
            # if(rt_item.dest_sock != -1):
            writes.append(rt_item.dest_sock)
    else:
        with wlock:
            rt_item.resp_client = msg
            # if(rt_item.client_sock != -1):
            writes.append(rt_item.client_sock)

######## WRITES
# @limits(calls=MAX_CALLS, period=MAX_PERIOD)
def handle_write_sock(sock, round_trips, writes, connections, wlock, rlock, proxy_cache):
    print("Writing to socket")
    for rtconn in round_trips:
        # sock.settimeout(TIME_OUT)
        if sock == rtconn.dest_sock:
            # print("BYTES SENT VS MSG SIZE:", sock.send(bytes(rtconn.req_dest, "utf-8")), "vs", len(bytes(rtconn.req_dest, "utf-8")))
            try:
                print("BYTES SENT VS MSG SIZE:", sock.send(rtconn.req_dest), "vs", len(rtconn.req_dest))
                # print("IN DESTSCK", rtconn.req_dest, "vs", rtconn.resp_client)
                add_to_cache(proxy_cache, rtconn.cache_key, rtconn.req_dest)
                # print("         SAVED", proxy_cache[rtconn.cache_key])
                ################# Add to the cache #################
            except Exception:
                sock.close()
                return

            with wlock:
                try:   
                    writes.remove(sock)
                except ValueError:
                    return
            with rlock:
                # if(sock != -1):
                connections.append(sock)
        elif sock == rtconn.client_sock and rtconn.resp_client != "":
            try:
                sock.send(rtconn.resp_client)
                # print("IN RESPCL", rtconn.req_dest, "vs", rtconn.resp_client)
                ################# Add to the cache #################
                add_to_cache(proxy_cache, rtconn.cache_key, rtconn.resp_client)
                # print("         SAVED", proxy_cache[rtconn.cache_key])
            except Exception:
                sock.close()
                return
            with wlock:
                try:
                    writes.remove(sock)
                except ValueError:
                    return
            with rlock:
                # if(sock != -1):
                connections.append(sock)
            # sock.close()

########################## Cache Functions #########################
def check_cache(proxy_cache, key):
    if key in proxy_cache:
        return proxy_cache[key]
    else:
        return NOT_CACHED

def add_to_cache(proxy_cache, key, value):
    if value != b'':
        proxy_cache[key] = value
    # print("         PROXY CACHE OF KEY", proxy_cache[key], "should contain", value)

######################### Helper Functions #########################
def handle_misc_exception(e, connections, writes, broken):
    print("Got an Exception", e)
    clean_connections(connections, writes, broken)
    # for conn in connections:
        # conn.close()
    # exit(1)

def parse_hostname_port(msg):
    hostname_index = msg.find("Host:")
    end_index = msg[hostname_index:].find("\r\n") + hostname_index
    hostname_port = msg[hostname_index+6:end_index]
    
    colon_index = hostname_port.find(":")
    if (colon_index != -1):
        hostname = hostname_port[:colon_index]
        port = int(hostname_port[colon_index+1:])
        # print("HOSTNAME:", hostname, "PORT:", port)
    else:
        hostname = hostname_port
        port = DEFAULT_PORT

    return hostname, port;

############################### Main ###############################
def main(argv, argc):
    if (argc != 2):
        print("Usage of command is", argv[0], "and PORTNO")
        exit(1)

    PORT = int(argv[1])
    server = set_up_server(PORT)

    proxy_cache = {}

    connections = [server]
    writes = []
    broken = []
    round_trips = []
    read_threads = []
    write_threads = []
    threads = read_threads + write_threads
    write_lock = threading.Lock()
    read_lock = threading.Lock()

    while True:
        # clean_connections(connections, writes, broken)
        try:
            read_socks, write_socks, error_socks = select.select(connections, writes, broken)
            count = 0
            for thread in threads:
                count += 1
                if thread.is_alive():
                    print("             Thread", count, "is alive.")
                else:
                    print("             Thread", count, "is dead.")

        # if round_trips != []:
        # print(len(read_socks), len(write_socks))
        
            i = 0
            for sock in read_socks:
                if (not handle_read_sock(sock, server, writes, connections, round_trips, read_threads, write_lock, read_lock, proxy_cache)):
                    print("FAILED")
                    continue;
                i =+ 1


            for sock in write_socks:
                print("STARTING WRITE THREAD")
                write_thread = threading.Thread(target=handle_write_sock, args=[sock, round_trips, writes, connections, write_lock, read_lock, proxy_cache])
                write_threads.append(write_thread)
                write_thread.start()
                # handle_write_sock(sock, round_trips, writes, connections)

            for thread in read_threads:
                thread.join()

            for thead in write_threads:
                thread.join()

        except Exception as e:
            handle_misc_exception(e, connections, writes, broken)

if __name__ == "__main__":
    main(sys.argv, len(sys.argv))
