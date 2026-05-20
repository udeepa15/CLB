BPF_CLANG ?= clang
BPF_CFLAGS ?= -O2 -g -target bpf
BPF_OBJ = bpf/counter_tc.o

all: $(BPF_OBJ)

bpf/counter_tc.o: bpf/counter_tc.c
	mkdir -p $(dir $@)
	$(BPF_CLANG) $(BPF_CFLAGS) -c $< -o $@

clean:
	rm -f $(BPF_OBJ)

.PHONY: all clean
