# Build the proof targets against vendored Picinae/Cloq via coq_makefile.
COQ_MAKEFILE := coq_makefile
VFILES := $(shell find targets lib -name '*.v' 2>/dev/null)

all: Makefile.coq
	$(MAKE) -f Makefile.coq

Makefile.coq: _CoqProject $(VFILES)
	$(COQ_MAKEFILE) -f _CoqProject $(VFILES) -o Makefile.coq

# Smoke target only (fast CI gate)
smoke: Makefile.coq
	$(MAKE) -f Makefile.coq targets/Addloop.vo

clean:
	-$(MAKE) -f Makefile.coq clean 2>/dev/null || true
	rm -f Makefile.coq Makefile.coq.conf

.PHONY: all smoke clean
