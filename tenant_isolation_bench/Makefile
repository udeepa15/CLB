BPF_CLANG ?= clang
BPF_CFLAGS ?= -O2 -g -target bpf -D__TARGET_ARCH_x86
BPF_INCLUDES ?= -I/usr/include -I/usr/include/$(shell uname -m)-linux-gnu

BPF_SRC := bpf/counter_tc.c
ISOLATED_OBJ := bpf/counter_tc_isolated.o
SHARED_OBJ := bpf/counter_tc_shared.o

all: $(ISOLATED_OBJ) $(SHARED_OBJ)

$(ISOLATED_OBJ): $(BPF_SRC)
	@mkdir -p $(dir $@)
	$(BPF_CLANG) $(BPF_CFLAGS) $(BPF_INCLUDES) -c $< -o $@

$(SHARED_OBJ): $(BPF_SRC)
	@mkdir -p $(dir $@)
	$(BPF_CLANG) $(BPF_CFLAGS) $(BPF_INCLUDES) -DUSE_SHARED=1 -c $< -o $@

clean:
	rm -f $(ISOLATED_OBJ) $(SHARED_OBJ)

.PHONY: all clean
