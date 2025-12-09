#include <mpi.h>
#include <iostream>
#include <unistd.h>
#include <cstring>
#include <vector>
#include <time.h>
#include <cstdlib> 
#include <cmath>  

int dsleep(double t)
{
    struct timespec req;
    
    req.tv_sec  = static_cast<time_t>(t);
    req.tv_nsec = static_cast<long>((t - req.tv_sec) * 1e9);

    // tv_nsec must be between 0 and 999999999
    if (req.tv_nsec < 0) req.tv_nsec = 0;
    if (req.tv_nsec > 999999999L) req.tv_nsec = 999999999L;

    return nanosleep(&req, nullptr);
}

void all2all_memcpy(const void* sendbuf, int sendcount, MPI_Datatype sendtype, void* recvbuf, int recvcount, MPI_Datatype recvtype, MPI_Comm comm){

    int rank, size;
    MPI_Comm_rank(comm, &rank);
    MPI_Comm_size(comm, &size);

    int datatype_size;
    MPI_Type_size(sendtype, &datatype_size);

    const char* sbuf = static_cast<const char*>(sendbuf);
    char* rbuf = static_cast<char*>(recvbuf);

    double mem_time = MPI_Wtime(); 
    // Copy local data directly (self-send)
    std::memcpy(rbuf + rank * datatype_size * recvcount,
                sbuf + rank * datatype_size * sendcount,
                sendcount * datatype_size);

}

void custom_alltoall(const void* sendbuf, int sendcount, MPI_Datatype sendtype,
                     void* recvbuf, int recvcount, MPI_Datatype recvtype, MPI_Comm comm) {
    int rank, size;
    MPI_Comm_rank(comm, &rank);
    MPI_Comm_size(comm, &size);

    int datatype_size;
    MPI_Type_size(sendtype, &datatype_size);

    const char* sbuf = static_cast<const char*>(sendbuf);
    char* rbuf = static_cast<char*>(recvbuf);

    std::vector<MPI_Request> requests;
    for (int i = 0; i < size; ++i) {
        if (i == rank) continue;

        MPI_Request req_recv;
        MPI_Request req_send;

        MPI_Isend(sbuf + i * datatype_size * sendcount, sendcount, sendtype, i, 0, comm, &req_send);
        MPI_Irecv(rbuf + i * datatype_size * recvcount, recvcount, recvtype, i, 0, comm, &req_recv);
        
        requests.push_back(req_send);
        requests.push_back(req_recv);
    }

    MPI_Waitall(static_cast<int>(requests.size()), requests.data(), MPI_STATUSES_IGNORE);
}

int main(int argc, char** argv) {
    MPI_Init(&argc, &argv);

    int size, rank;
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);

    const int BUFFER_SIZE = 2 * 1024 * 1024;  // bytes per peer 16MiB

    // Each process will send a chunk to every other process
    unsigned char *send_buffer = (unsigned char*) malloc(BUFFER_SIZE*size); 
    unsigned char *recv_buffer = (unsigned char*) malloc(BUFFER_SIZE*size);
    if (send_buffer == NULL || recv_buffer == NULL) {
        fprintf(stderr, "Memory allocation failed!\n");
        MPI_Abort(MPI_COMM_WORLD, 1);
        return -1;
    }

    srand(time(NULL)*rank); 
    for (int i = 0; i < BUFFER_SIZE*size; i++) {
        send_buffer[i] = rand()*rank % size; 
    }

    double burst_pause=1e-6;
    double burst_length=1e-2*3; //30ms
    if(argc >= 2){
      burst_pause = atof(argv[1]);
    }
    if(argc >= 3){
      burst_length = atof(argv[2]);
    }


    bool burst_pause_rand = false;
    double burst_start_time;
    double measure_start_time;
    double burst_length_mean=burst_length;
    double burst_pause_mean=burst_pause;
    int burst_cont=0;

    while (1) {
        burst_start_time=MPI_Wtime();
        do {
            all2all_memcpy(send_buffer, BUFFER_SIZE, MPI_BYTE, recv_buffer, BUFFER_SIZE, MPI_BYTE, MPI_COMM_WORLD);
            custom_alltoall(send_buffer, BUFFER_SIZE, MPI_BYTE, recv_buffer, BUFFER_SIZE, MPI_BYTE, MPI_COMM_WORLD);

            if(burst_length!=0){ /*bcast needed for synch if bursts timed*/
                if(rank == 0){ /*master decides if burst should be continued*/
                    burst_cont=((MPI_Wtime()-burst_start_time)<burst_length);
                }
                MPI_Bcast(&burst_cont,1, MPI_INT, 0, MPI_COMM_WORLD); /*bcast the masters decision*/
            }
        } while (burst_cont);

        if(burst_pause!=0){
            dsleep(burst_pause);
        }
    }


    MPI_Finalize();
    return 0;
}