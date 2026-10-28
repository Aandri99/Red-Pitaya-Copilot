CXX ?= g++
CPPFLAGS ?=
CXXFLAGS ?= -O2 -std=c++17 -fPIC -Wall -Wextra -Wpedantic -Werror
LDFLAGS ?= -shared

TARGET := controllerhf.so
SOURCES := src/main.cpp

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SOURCES) src/controller_contract.h
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SOURCES) $(LDFLAGS) -o $(TARGET)

clean:
	rm -f $(TARGET)
