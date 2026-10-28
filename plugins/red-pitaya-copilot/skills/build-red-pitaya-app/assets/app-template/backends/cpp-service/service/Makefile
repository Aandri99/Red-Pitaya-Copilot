CXX ?= g++
CPPFLAGS ?=
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -Wpedantic -Werror
LDFLAGS ?=

TARGET := build/__APP_SLUG__-service
SOURCE := main.cpp

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(SOURCE)
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SOURCE) $(LDFLAGS) -o $(TARGET)

clean:
	rm -f $(TARGET)
